"""Feishu answer card Audit 摘要 safety tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi.testclient import TestClient

from project_lens.domain.models import (
    AgentRun,
    Claim,
    ClaimType,
    Evidence,
    EvidenceType,
    ProjectAnswer,
    ProjectRef,
    SourceRef,
)
from project_lens.integrations.feishu.audit_summary import build_feishu_audit_summary
from project_lens.integrations.feishu.cards import render_answer_card
from project_lens.main import create_app
from project_lens.runtime.events import AgentEvent, AgentEventType
from project_lens.workflow.context_prompt import render_context_prompt
from tests.test_context_prompt import _pack
from tests.test_feishu_integration import (
    TRACEBACK,
    _configure_verifier,
    _last_card_text,
    _message_payload,
)


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _answer(*, skill: str = "incident_diagnosis") -> ProjectAnswer:
    project = _project()
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local", source_id="src/order_service.py"),
        content="SECRET_BODY_" + ("coupon null dereference detail " * 40),
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    return ProjectAnswer(
        project=project,
        skill=skill,
        confidence=0.7,
        status="identified",
        business_summary="下单可能失败",
        technical_summary="AttributeError on coupon",
        claims=(
            Claim(
                text="optional coupon can be None",
                type=ClaimType.FACT,
                evidence_ids=(evidence.id,),
            ),
        ),
        evidence=(evidence,),
        unknowns=("缺少完整日志窗口",),
        recommended_actions=(),
    )


def _card_blob(card: dict[str, object]) -> str:
    return json.dumps(card, ensure_ascii=False)


def test_render_answer_card_includes_audit_section() -> None:
    run = AgentRun(
        project=_project(),
        user_id="u1",
        channel_id="chat-1",
        question="架构是什么？",
    )
    answer = _answer(skill="architecture")
    events = (
        AgentEvent(
            run_id=run.id,
            trace_id=run.trace_id,
            type=AgentEventType.RUN_STATUS_CHANGED,
            payload={
                "status": "analyzing",
                "skill": "architecture",
                "evidence_count": 3,
                "graph_path_count": 1,
                "memory_count": 0,
                "ops_signal_count": 0,
                "context_pack": {
                    "access_scope": "project:payment:read",
                    "allow_apply": False,
                    "skill": "architecture",
                },
                "model_adapter": {
                    "provider": "stub.v1",
                    "model_name": "stub-v1",
                    "retries": 0,
                    "timed_out": False,
                    "allow_apply": False,
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 8,
                        "total_tokens": 20,
                    },
                },
            },
        ),
    )
    summary = build_feishu_audit_summary(run, answer, events=events)
    card = render_answer_card(run, answer, audit_summary=summary)
    text = "\n".join(
        element.get("content", "")
        for element in card["elements"]
        if isinstance(element, dict)
    )
    assert "**调试信息**" in text
    assert "provider: stub.v1" in text
    assert "model_name: stub-v1" in text
    assert "evidence_count: 3" in text
    assert "allow_apply: False" in text
    assert f"trace_id: {run.trace_id}" in text
    assert "**当前 Skill**" not in text
    assert card["header"]["title"]["content"] == "ProjectLens 项目地图"


def test_audit_summary_uses_context_prompt_refs_without_prompt_body() -> None:
    pack = _pack(long_evidence=True)
    prompt = render_context_prompt(pack, evidence_snippet_chars=48)
    run = AgentRun(
        id=pack.anchors.run_id,
        project=pack.anchors.project,
        user_id="u1",
        question=pack.anchors.question,
        trace_id=pack.anchors.trace_id,
    )
    answer = _answer()
    summary = build_feishu_audit_summary(
        run,
        answer,
        context_pack_refs=pack.audit_refs(),
        context_prompt_refs=prompt.audit_refs,
        model_adapter_refs={
            "provider": "stub.v1",
            "model_name": "stub-v1",
            "retries": 0,
            "timed_out": False,
            "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
        },
    )
    md = summary.to_markdown()
    assert "provider: stub.v1" in md
    assert "evidence_count:" in md
    assert prompt.as_text() not in md
    assert pack.evidence[0].content not in md
    assert summary.allow_apply == "False"


def test_audit_summary_omits_secrets_and_unknown_when_missing() -> None:
    run = AgentRun(
        project=_project(),
        user_id="u1",
        question="hello",
    )
    answer = _answer()
    summary = build_feishu_audit_summary(
        run,
        answer,
        model_adapter_refs={
            "provider": "stub.v1",
            "api_key": "sk-secret-should-not-appear",
            "token": "bearer-xyz",
            "model_name": "stub-v1",
        },
    )
    md = summary.to_markdown()
    assert "sk-secret" not in md
    assert "bearer-xyz" not in md
    assert "api_key" not in md
    assert "graph_path_count: unknown" in md
    assert summary.allow_apply == "False"


def test_audit_card_hides_prompt_and_evidence_body() -> None:
    pack = _pack(long_evidence=True)
    prompt = render_context_prompt(pack, evidence_snippet_chars=64)
    full_body = pack.evidence[0].content
    run = AgentRun(
        id=pack.anchors.run_id,
        project=pack.anchors.project,
        user_id="u1",
        question=pack.anchors.question,
        trace_id=pack.anchors.trace_id,
    )
    answer = _answer()
    answer = answer.model_copy(update={"evidence": pack.evidence})
    summary = build_feishu_audit_summary(
        run,
        answer,
        model_adapter_refs={
            "provider": "stub.v1",
            "model_name": "stub-v1",
            "retries": 0,
            "timed_out": False,
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
            "allow_apply": False,
        },
        context_pack_refs=pack.audit_refs(),
    )
    card = render_answer_card(run, answer, audit_summary=summary)
    blob = _card_blob(card)
    assert full_body not in blob
    assert prompt.as_text() not in blob
    assert "## Session Summary" not in blob
    assert "tool_boundary=tool_gateway" not in blob or "调试信息" in blob
    # Prompt L0 dump should not be pasted wholesale.
    assert "access_permissions=" not in blob


def _assert_audit_safe(card_text: str, *, run_id: str, app) -> None:
    assert "**调试信息**" in card_text
    assert "provider:" in card_text
    assert "model_name:" in card_text
    assert "evidence_count:" in card_text
    assert "allow_apply: False" in card_text
    run = app.state.run_service.get(UUID(run_id))
    assert run is not None
    assert f"trace_id: {run.trace_id}" in card_text
    audit_block = card_text.split("**调试信息**", 1)[1]
    assert "api_key" not in audit_block.lower()
    assert "client_secret" not in audit_block.lower()
    assert "sk-secret" not in audit_block.lower()
    assert "bearer " not in audit_block.lower()
    assert "PROJECT_LENS_" not in audit_block
    workflow = app.state.run_service._workflow
    if workflow.last_context_prompt is not None:
        assert workflow.last_context_prompt.as_text() not in card_text
    if workflow.last_context_pack is not None:
        for item in workflow.last_context_pack.evidence:
            if len(item.content) > 80:
                assert item.content not in card_text


def _assert_no_apply_button(card: dict[str, object]) -> None:
    for element in card.get("elements", []):
        if not isinstance(element, dict) or element.get("tag") != "action":
            continue
        for action in element.get("actions", []):
            value = str(action.get("value", {})).lower()
            label = str(action.get("text", {})).lower()
            assert "apply" not in value
            assert "立即应用" not in label
            assert "engineering_apply" not in value


def test_feishu_ordinary_answer_shows_audit_summary() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)
    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="audit-arch", text="这个项目的架构是什么？"),
    )
    assert response.status_code == 200
    card_text = _last_card_text(app)
    _assert_audit_safe(card_text, run_id=response.json()["run_id"], app=app)
    card = app.state.feishu_messenger.messages[1].content
    _assert_no_apply_button(card)
    assert "stub" in card_text


def test_feishu_engineering_proposal_shows_audit_summary() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)
    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="audit-eng", text=TRACEBACK),
    )
    assert response.status_code == 200
    card_text = _last_card_text(app)
    assert "**修复提案**" in card_text
    _assert_audit_safe(card_text, run_id=response.json()["run_id"], app=app)
    card = app.state.feishu_messenger.messages[1].content
    _assert_no_apply_button(card)
    assert "立即应用" not in card_text


def test_feishu_knowledge_gap_answer_shows_audit_summary() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)
    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="audit-gap", text="知识库缺什么"),
    )
    assert response.status_code == 200
    card_text = _last_card_text(app)
    assert "**知识库缺口视图**" in card_text or "project_knowledge" in card_text
    _assert_audit_safe(card_text, run_id=response.json()["run_id"], app=app)
    card = app.state.feishu_messenger.messages[1].content
    _assert_no_apply_button(card)


def test_feishu_adapter_does_not_import_evidence_index_for_audit() -> None:
    import inspect

    from project_lens.integrations.feishu import audit_summary, service

    assert "from project_lens.context.store import EvidenceIndex" not in inspect.getsource(
        audit_summary
    )
    assert "EvidenceIndex" not in inspect.getsource(service).split('"""', 2)[-1]
    assert "build_feishu_audit_summary" in inspect.getsource(service)
    assert "authorized_evidence" not in inspect.getsource(audit_summary)
