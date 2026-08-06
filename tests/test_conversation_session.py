"""ConversationSession lifecycle and L1/L2 updates."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from project_lens.application.conversation_service import ConversationService
from project_lens.domain.conversation import ConversationTurn, empty_summary
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


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _answer(*, skill: str = "incident_diagnosis") -> ProjectAnswer:
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=_project(),
        source=SourceRef(system="local", source_id="fixture"),
        content="coupon null",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    claim = Claim(
        text="coupon may be null",
        type=ClaimType.FACT,
        evidence_ids=(evidence.id,),
        grade=EvidenceGrade.B,
    )
    return ProjectAnswer(
        project=_project(),
        skill=skill,
        confidence=0.7,
        status="partial",
        business_summary="下单失败风险",
        technical_summary="Null attribute access",
        claims=(claim,),
        evidence=(evidence,),
        unknowns=("缺少完整日志窗口",),
        recommended_actions=(
            ActionProposal(
                title="排查 coupon 空指针",
                description="证据指向可选字段未校验",
                tool_name="search_context",
                arguments={"q": "coupon"},
                requires_approval=False,
            ),
        ),
    )


def test_get_or_create_returns_new_session() -> None:
    service = ConversationService()
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
    )

    assert session.session_id is not None
    assert session.project_ref == project
    assert session.last_run_id is None
    assert session.recent_turns == ()
    assert session.summary.active_skill is None
    assert session.task_scratchpad == {}
    assert session.summary.project == empty_summary(project).project
    assert session.summary.verified_claim_ids == ()
    assert session.summary.evidence_ids == ()


def test_get_or_create_reuses_binding() -> None:
    service = ConversationService()
    project = _project()
    first = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
    )
    second = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
    )
    assert first.session_id == second.session_id


def test_record_turn_updates_recent_turns_and_last_run_id() -> None:
    service = ConversationService()
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
    )
    run_id = uuid4()
    updated = service.record_turn(
        session,
        user_id="user-1",
        text="那是谁改的？",
        rewritten_question="基于上一轮故障诊断，查找相关 commit、任务、负责人和变更影响。",
        run_id=run_id,
        answer=_answer(),
    )

    assert updated.last_run_id == run_id
    assert len(updated.recent_turns) == 1
    turn = updated.recent_turns[0]
    assert isinstance(turn, ConversationTurn)
    assert turn.run_id == run_id
    assert turn.text == "那是谁改的？"
    assert turn.rewritten_question is not None
    assert "commit" in turn.rewritten_question
    assert updated.summary.active_skill == "incident_diagnosis"
    assert updated.summary.verified_claim_ids
    assert updated.summary.evidence_ids
    assert "缺少完整日志窗口" in updated.summary.unknowns
    assert "排查 coupon 空指针" in updated.summary.next_actions


def test_recent_turns_slide_and_merge_into_summary() -> None:
    service = ConversationService(recent_turn_limit=2)
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
    )
    for index in range(3):
        session = service.record_turn(
            session,
            user_id="user-1",
            text=f"question-{index}",
            rewritten_question=None,
            run_id=uuid4(),
            answer=_answer() if index == 0 else None,
        )

    assert len(session.recent_turns) == 2
    assert session.recent_turns[0].text == "question-1"
    assert session.recent_turns[1].text == "question-2"
    assert session.summary.active_topic.get("compressed_turns") == "1"
    assert "question-0" in session.summary.active_topic.get("last_compressed", "")


def test_session_summary_is_not_project_memory() -> None:
    service = ConversationService()
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
    )
    updated = service.record_turn(
        session,
        user_id="user-1",
        text="故障",
        rewritten_question=None,
        run_id=uuid4(),
        answer=_answer(),
    )
    # Runtime summary only — no memory id / approved fact payload.
    assert not hasattr(updated.summary, "memory_id")
    assert updated.summary.active_skill == "incident_diagnosis"
    assert "memory:" not in " ".join(updated.summary.artifact_refs)
