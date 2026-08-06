"""Probe tests for deterministic L1->L2 / L3 context compression."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from project_lens.application.conversation_service import ConversationService
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
from project_lens.workflow.context_compression import (
    compress_overflow_into_summary,
    compress_scratchpad,
    extract_pinned_from_scratchpad,
    extract_pinned_from_text,
    pinned_probe_values,
)
from project_lens.workflow.context_pack import TaskScratchpad, task_scratchpad_from_session


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _answer(
    *,
    evidence_id=None,
    proposal_id=None,
    file_path: str = "src/order_service.py",
    commit_sha: str = "abcdef1234567",
) -> ProjectAnswer:
    evidence = Evidence(
        id=evidence_id or uuid4(),
        type=EvidenceType.CODE,
        project=_project(),
        source=SourceRef(system="local", source_id="order_service.py"),
        content=f"def create_order(): ... commit {commit_sha}",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
        metadata={
            "file": file_path,
            "commit_sha": commit_sha,
            "symbol": "create_order",
            "incident_id": "INC-42",
        },
    )
    action = ActionProposal(
        id=proposal_id or uuid4(),
        title="修复提案",
        tool_name="engineering_proposal",
        requires_approval=True,
        arguments={
            "explanation": "Add null guard",
            "affected_paths": [file_path],
            "diff_summary": f"+ guard in {file_path}",
            "test_passed": True,
            "can_apply": False,
        },
    )
    return ProjectAnswer(
        project=_project(),
        skill="incident_diagnosis",
        confidence=0.8,
        status="identified",
        business_summary="下单失败，需人工确认修复",
        technical_summary=f"null coupon in {file_path}",
        claims=(
            Claim(
                text="coupon may be null",
                type=ClaimType.FACT,
                evidence_ids=(evidence.id,),
                grade=EvidenceGrade.B,
            ),
        ),
        evidence=(evidence,),
        unknowns=("缺少完整日志",),
        recommended_actions=(action,),
    )


def test_extract_pinned_ids_from_text() -> None:
    evidence_id = uuid4()
    text = (
        f"see evidence {evidence_id} commit deadbeef "
        f"path src/order_service.py docx_payment_owners INC-99 "
        f"File \"order_service.py\", line 16, in create_order\n"
        f"proposal:{uuid4()}"
    )
    pinned = extract_pinned_from_text(text)
    probe = pinned_probe_values(pinned)
    assert str(evidence_id) in probe
    assert "deadbeef" in probe
    assert "src/order_service.py" in probe or "order_service.py" in probe
    assert "docx_payment_owners" in probe
    assert "INC-99" in probe
    assert "create_order" in probe


def test_l1_overflow_increments_compression_cycle_and_keeps_pins() -> None:
    service = ConversationService(recent_turn_limit=2)
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="u1",
        project=project,
    )
    evidence_id = uuid4()
    proposal_id = uuid4()
    file_path = "src/order_service.py"
    commit_sha = "abc1234deadbeef"

    session = service.record_turn(
        session,
        user_id="u1",
        text=f"traceback in {file_path} commit {commit_sha} docx_payment_owners",
        rewritten_question=None,
        run_id=uuid4(),
        answer=_answer(
            evidence_id=evidence_id,
            proposal_id=proposal_id,
            file_path=file_path,
            commit_sha=commit_sha,
        ),
        task_state=TaskScratchpad(
            phase="Validate",
            files_read=(file_path,),
            patch_plan="null guard",
            diff_summary=f"+ guard {file_path}",
            approval_status="pending",
            allow_apply=False,
            notes={"engineering_action_id": str(proposal_id)},
        ),
    )
    assert session.summary.compression_cycle == 0
    assert evidence_id in session.summary.evidence_ids

    # Push the first turn out of L1.
    for index in range(2):
        session = service.record_turn(
            session,
            user_id="u1",
            text=f"followup-{index}",
            rewritten_question=None,
            run_id=uuid4(),
        )

    assert len(session.recent_turns) == 2
    assert session.summary.compression_cycle >= 1
    assert session.summary.active_topic.get("compression_cycle") == str(
        session.summary.compression_cycle
    )
    probe = pinned_probe_values(session.summary.pinned_ids)
    assert str(evidence_id) in probe
    assert str(proposal_id) in probe
    assert file_path in probe
    assert commit_sha in probe
    assert "docx_payment_owners" in probe or any(
        "docx_payment_owners" in item for item in session.summary.pinned_ids.doc_tokens
    )
    assert "create_order" in probe
    assert "INC-42" in probe
    # Runtime summary only — never ProjectMemory promotion.
    assert not hasattr(session.summary, "memory_id")
    assert session.summary.last_assistant_summary is not None


def test_overflow_is_incremental_not_full_rewrite() -> None:
    service = ConversationService(recent_turn_limit=2)
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="u1",
        project=project,
    )
    first_evidence = uuid4()
    session = service.record_turn(
        session,
        user_id="u1",
        text="first",
        rewritten_question=None,
        run_id=uuid4(),
        answer=_answer(evidence_id=first_evidence, proposal_id=uuid4()),
    )
    second_evidence = uuid4()
    session = service.record_turn(
        session,
        user_id="u1",
        text="second",
        rewritten_question=None,
        run_id=uuid4(),
        answer=_answer(evidence_id=second_evidence, proposal_id=uuid4()),
    )
    # Overflow first turn only.
    session = service.record_turn(
        session,
        user_id="u1",
        text="third",
        rewritten_question=None,
        run_id=uuid4(),
    )
    assert session.summary.compression_cycle == 1
    assert session.summary.active_topic["compressed_turns"] == "1"
    assert "first" in session.summary.active_topic["last_compressed"]
    probe = pinned_probe_values(session.summary.pinned_ids)
    assert str(first_evidence) in probe
    assert str(second_evidence) in probe


def test_l3_scratchpad_compression_keeps_file_and_proposal() -> None:
    proposal_id = str(uuid4())
    pad = TaskScratchpad(
        phase="Validate",
        files_read=("src/order_service.py",),
        patch_plan="guard",
        diff_summary="+x",
        tool_audit_events=tuple(f"tool_completed:step-{i}" for i in range(30)),
        notes={"engineering_action_id": proposal_id},
        sync_state={"doc:docx_payment_owners": "success@9"},
        allow_apply=False,
    )
    compressed = compress_scratchpad(pad, max_tool_events=5)
    assert compressed is not None
    assert len(compressed.tool_audit_events) == 5
    assert compressed.files_read == ("src/order_service.py",)
    assert compressed.notes["engineering_action_id"] == proposal_id
    assert compressed.sync_state["doc:docx_payment_owners"] == "success@9"
    assert int(compressed.notes["compressed_tool_events"]) == 25
    pins = extract_pinned_from_scratchpad(compressed)
    probe = pinned_probe_values(pins)
    assert "src/order_service.py" in probe
    assert proposal_id in probe
    assert "docx_payment_owners" in probe


def test_compress_overflow_helper_pins_run_id() -> None:
    from project_lens.domain.conversation import ConversationTurn, empty_summary

    run_id = uuid4()
    summary = empty_summary(_project())
    overflow = (
        ConversationTurn(
            run_id=run_id,
            user_id="u1",
            text="see abcdef0 and src/payment.py",
        ),
    )
    updated = compress_overflow_into_summary(summary, overflow)
    assert updated.compression_cycle == 1
    assert str(run_id) in updated.pinned_ids.run_ids
    assert "abcdef0" in updated.pinned_ids.commit_shas
    assert any("payment.py" in path for path in updated.pinned_ids.file_paths)


def test_session_scratchpad_survives_after_compression() -> None:
    service = ConversationService(recent_turn_limit=2)
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="u1",
        project=project,
    )
    file_path = "examples/payment_service/src/order_service.py"
    session = service.record_turn(
        session,
        user_id="u1",
        text="怎么修？",
        rewritten_question=None,
        run_id=uuid4(),
        answer=_answer(file_path=file_path),
        task_state=TaskScratchpad(
            files_read=(file_path,),
            patch_plan="null guard",
            diff_summary="+ if coupon is None",
            approval_status="pending",
        ),
    )
    for index in range(3):
        session = service.record_turn(
            session,
            user_id="u1",
            text=f"ping-{index}",
            rewritten_question=None,
            run_id=uuid4(),
        )
    pad = task_scratchpad_from_session(session)
    assert pad is not None
    assert file_path in pad.files_read
    assert session.summary.compression_cycle >= 1
    assert file_path in session.summary.pinned_ids.file_paths
