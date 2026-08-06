"""TaskScratchpad compression and persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from project_lens.application.conversation_service import ConversationService
from project_lens.domain.feishu_doc_sync import FeishuDocSyncStatus, FeishuDocSyncStatusValue
from project_lens.domain.models import (
    ActionProposal,
    Claim,
    ClaimType,
    Evidence,
    EvidenceGrade,
    EvidenceType,
    ProjectAnswer,
    ProjectRef,
    SourceRef,
)
from project_lens.domain.ops import OpsFinding, OpsQuery, OpsSignalKind, OperationalSignal
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.main import create_app
from project_lens.runtime.events import AgentEvent, AgentEventType
from project_lens.workflow.context_pack import TaskScratchpad, task_scratchpad_from_session
from project_lens.workflow.task_scratchpad import (
    compress_events_to_scratchpad,
    merge_scratchpads,
    scratchpad_from_answer,
    scratchpad_from_engineering_action,
    scratchpad_from_ops_finding,
    scratchpad_from_sync_statuses,
)


TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def test_engineering_action_populates_files_patch_diff_tests_approval() -> None:
    action = ActionProposal(
        title="修复提案",
        description="null guard",
        tool_name="engineering_proposal",
        requires_approval=True,
        arguments={
            "explanation": "Add null guard for optional coupon",
            "affected_paths": ["src/order_service.py"],
            "patch_plan_text": "Guard coupon: add null check\n- src/order_service.py",
            "diff_summary": "+ if request.coupon is None",
            "test_commands": ['python -c "print(\'ok\')"'],
            "test_passed": True,
            "test_output_summary": "ok",
            "failed_attempts": [],
            "can_apply": False,
            "allow_apply": False,
        },
    )
    pad = scratchpad_from_engineering_action(action)
    assert pad.phase == "Validate"
    assert pad.files_read == ("src/order_service.py",)
    assert pad.patch_plan == "Guard coupon: add null check\n- src/order_service.py"
    assert pad.diff_summary == "+ if request.coupon is None"
    assert pad.test_commands == ('python -c "print(\'ok\')"',)
    assert pad.test_results[0] == "test_passed=True"
    assert pad.approval_status == "pending"
    assert pad.allow_apply is False


def test_compress_events_keeps_append_only_and_merges_answer() -> None:
    run_id = uuid4()
    trace_id = uuid4()
    events = (
        AgentEvent(
            run_id=run_id,
            trace_id=trace_id,
            type=AgentEventType.RUN_STATUS_CHANGED,
            payload={
                "status": "analyzing",
                "evidence_count": 3,
                "ops_signal_count": 1,
                "context_pack": {"session_id": str(uuid4()), "skill": "incident_diagnosis"},
            },
        ),
        AgentEvent(
            run_id=run_id,
            trace_id=trace_id,
            type=AgentEventType.TOOL_COMPLETED,
            payload={"tool_name": "validate_patch_plan"},
        ),
    )
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=_project(),
        source=SourceRef(system="local", source_id="order_service.py"),
        content="coupon",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    answer = ProjectAnswer(
        project=_project(),
        skill="incident_diagnosis",
        confidence=0.7,
        status="identified",
        business_summary="下单失败",
        technical_summary="null coupon",
        claims=(
            Claim(
                text="coupon may be null",
                type=ClaimType.FACT,
                evidence_ids=(evidence.id,),
                grade=EvidenceGrade.B,
            ),
        ),
        evidence=(evidence,),
        recommended_actions=(
            ActionProposal(
                title="修复提案",
                tool_name="engineering_proposal",
                requires_approval=True,
                arguments={
                    "explanation": "guard coupon",
                    "affected_paths": ["src/order_service.py"],
                    "diff_summary": "+ guard",
                    "test_passed": True,
                    "can_apply": False,
                },
            ),
        ),
    )
    pad = compress_events_to_scratchpad(events, answer=answer)
    assert pad is not None
    assert "tool_completed:validate_patch_plan" in pad.tool_audit_events
    assert pad.files_read == ("src/order_service.py",)
    assert pad.diff_summary == "+ guard"
    assert pad.allow_apply is False
    assert pad.notes.get("evidence_count") == "3"


def test_ops_and_sync_scratchpads() -> None:
    project = _project()
    finding = OpsFinding(
        query=OpsQuery(
            project=project,
            start=datetime(2026, 8, 1, tzinfo=timezone.utc),
            end=datetime(2026, 8, 2, tzinfo=timezone.utc),
        ),
        signals=(
            OperationalSignal(
                kind=OpsSignalKind.LOG,
                project=project,
                environment="production",
                observed_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
                access_scope="project:payment:read",
                summary="error spike",
            ),
        ),
        summary="1 log signal",
    )
    ops = scratchpad_from_ops_finding(finding)
    assert ops is not None
    assert ops.notes["ops_signal_count"] == "1"

    sync = scratchpad_from_sync_statuses(
        (
            FeishuDocSyncStatus(
                project=project,
                doc_token="docx_a",
                revision="9",
                status=FeishuDocSyncStatusValue.SUCCESS,
                title="owners",
                doc_url="https://feishu.cn/docx/docx_a",
                access_scope="project:payment:read",
                last_success_revision="9",
            ),
            FeishuDocSyncStatus(
                project=project,
                doc_token="docx_b",
                revision=None,
                status=FeishuDocSyncStatusValue.FAILED,
                error="timeout",
                last_success_revision="3",
            ),
        )
    )
    assert sync.phase == "DocSyncStatus"
    assert sync.sync_state["failed_count"] == "1"
    assert sync.sync_state["doc:docx_a"] == "success@9"
    assert sync.sync_state["url:docx_a"] == "https://feishu.cn/docx/docx_a"
    assert sync.sync_state["scope:docx_a"] == "project:payment:read"
    assert sync.sync_state["last_ok:docx_b"] == "3"
    assert "timeout" in sync.sync_state["error:docx_b"]


def test_session_persists_task_scratchpad_without_memory_write() -> None:
    service = ConversationService()
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="u1",
        project=project,
    )
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local", source_id="x"),
        content="x",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefbb",
    )
    answer = ProjectAnswer(
        project=project,
        skill="incident_diagnosis",
        confidence=0.5,
        status="identified",
        business_summary="b",
        technical_summary="t",
        claims=(
            Claim(
                text="fact",
                type=ClaimType.FACT,
                evidence_ids=(evidence.id,),
                grade=EvidenceGrade.B,
            ),
        ),
        evidence=(evidence,),
        recommended_actions=(
            ActionProposal(
                title="修复提案",
                tool_name="engineering_proposal",
                requires_approval=True,
                arguments={
                    "explanation": "guard",
                    "affected_paths": ["src/order_service.py"],
                    "diff_summary": "+x",
                    "test_passed": False,
                    "can_apply": True,  # must be forced off in scratchpad
                },
            ),
        ),
    )
    updated = service.record_turn(
        session,
        user_id="u1",
        text="怎么修？",
        rewritten_question=None,
        run_id=uuid4(),
        answer=answer,
    )
    pad = task_scratchpad_from_session(updated)
    assert pad is not None
    assert pad.files_read == ("src/order_service.py",)
    assert pad.diff_summary == "+x"
    assert pad.allow_apply is False
    assert "engineering_validation_failed" in pad.failed_attempts
    assert updated.summary.active_skill == "incident_diagnosis"


def test_feishu_incident_run_writes_scratchpad_on_session_and_event() -> None:
    app = create_app()
    verifier = app.state.feishu_event_service._verifier
    verifier._verification_token = "project-lens-local-token"
    verifier._signing_secret = None
    project = _project()
    app.state.feishu_event_service._identity_mapper = parse_project_bindings(
        "",
        default_project=project,
    )
    app.state.feishu_messenger = RecordingFeishuMessenger()
    app.state.feishu_event_service._messenger = app.state.feishu_messenger
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json={
            "schema": "2.0",
            "token": "project-lens-local-token",
            "header": {
                "event_id": "scratchpad-event",
                "event_type": "im.message.receive_v1",
                "tenant_key": "demo",
            },
            "event": {
                "sender": {"sender_id": {"user_id": "feishu-user-1"}},
                "message": {
                    "message_id": "message-scratchpad",
                    "chat_id": "chat-1",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": TRACEBACK}),
                },
            },
        },
    )
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    completed = next(
        event
        for event in app.state.run_service.events(UUID(run_id))
        if event.type.value == "run_completed"
    )
    scratch = completed.payload["task_scratchpad"]
    assert scratch["allow_apply"] is False
    assert scratch["files_read"]
    assert scratch["diff_summary"]
    assert scratch["approval_status"] == "pending"

    session = app.state.conversation_service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="feishu-user-1",
        project=project,
    )
    pad = task_scratchpad_from_session(session)
    assert pad is not None
    assert pad.files_read
    assert pad.diff_summary
    assert pad.allow_apply is False


def test_merge_never_enables_apply() -> None:
    merged = merge_scratchpads(
        TaskScratchpad(allow_apply=True, phase="X"),
        TaskScratchpad(allow_apply=True, files_read=("a.py",)),
    )
    assert merged is not None
    assert merged.allow_apply is False
    assert merged.files_read == ("a.py",)


def test_scratchpad_from_answer_without_engineering() -> None:
    answer = ProjectAnswer(
        project=_project(),
        skill="project_knowledge",
        confidence=0.4,
        status="partial",
        business_summary="b",
        technical_summary="t",
    )
    pad = scratchpad_from_answer(answer)
    assert pad is not None
    assert pad.phase == "Answered"
    assert pad.files_read == ()
    assert pad.allow_apply is False
