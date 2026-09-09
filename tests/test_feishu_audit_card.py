"""Feishu answer card Audit 摘要 safety tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

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
from project_lens.runtime.events import AgentEvent, AgentEventType
from project_lens.workflow.context_prompt import render_context_prompt
from tests.test_context_prompt import _pack


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
    assert card["header"]["title"]["content"] == "ProjectLens 项目回答"


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


def test_audit_summary_uses_hermes_run_runtime_not_answer_skill() -> None:
    run = AgentRun(
        project=_project(),
        user_id="u1",
        question="介绍一下项目",
        runtime="hermes",
    )

    summary = build_feishu_audit_summary(
        run,
        _answer(skill="project_investigation"),
    )

    assert summary.agent_mode == "hermes"


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


def test_feishu_adapter_does_not_import_evidence_index_for_audit() -> None:
    import inspect

    from project_lens.integrations.feishu import audit_summary, service

    assert "from project_lens.context.store import EvidenceIndex" not in inspect.getsource(
        audit_summary
    )
    assert "EvidenceIndex" not in inspect.getsource(service).split('"""', 2)[-1]
    assert "authorized_evidence" not in inspect.getsource(audit_summary)
    # Hermes-only service must not reach into EvidenceIndex for card audit.
    assert "build_feishu_audit_summary" in inspect.getsource(audit_summary)
