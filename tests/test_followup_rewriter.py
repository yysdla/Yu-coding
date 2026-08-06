"""Rule-based FollowupRewriter coverage."""

from __future__ import annotations

from datetime import datetime, timezone

from project_lens.domain.conversation import (
    ConversationSession,
    ConversationSummary,
    ConversationTurn,
    empty_summary,
)
from project_lens.domain.models import ProjectRef
from project_lens.workflow.followup import FollowupRewriter
from project_lens.workflow.skills import ProjectSkill

_TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def _session(
    *,
    skill: str | None,
    prior_text: str | None = None,
) -> ConversationSession:
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    summary = ConversationSummary(
        project=project,
        active_skill=skill,
        updated_at=datetime.now(timezone.utc),
    )
    turns: tuple[ConversationTurn, ...] = ()
    if prior_text is not None:
        turns = (
            ConversationTurn(user_id="user-1", text=prior_text),
        )
    return ConversationSession(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
        recent_turns=turns,
        summary=summary if skill is not None else empty_summary(project),
    )


def test_who_changed_after_incident() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite(
        "那是谁改的？",
        _session(skill=ProjectSkill.INCIDENT_DIAGNOSIS.value),
    )
    assert rewritten is not None
    assert "故障诊断" in rewritten
    assert "commit" in rewritten
    assert "负责人" in rewritten


def test_who_changed_after_version() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite(
        "那是谁改的？",
        _session(skill=ProjectSkill.VERSION_CHANGE.value),
    )
    assert rewritten is not None
    assert "版本变更" in rewritten
    assert "commit" in rewritten


def test_impact_where_after_version() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite(
        "影响哪里？",
        _session(skill=ProjectSkill.VERSION_CHANGE.value),
    )
    assert rewritten is not None
    assert "版本变更" in rewritten
    assert "受影响服务" in rewritten
    assert "回归" in rewritten


def test_impact_where_after_incident() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite(
        "影响哪里？",
        _session(skill=ProjectSkill.INCIDENT_DIAGNOSIS.value),
    )
    assert rewritten is not None
    assert "故障诊断" in rewritten
    assert "受影响服务" in rewritten


def test_how_to_fix_is_read_only_no_apply() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite(
        "怎么修？",
        _session(
            skill=ProjectSkill.INCIDENT_DIAGNOSIS.value,
            prior_text=_TRACEBACK,
        ),
    )
    assert rewritten is not None
    assert "Engineering" in rewritten or "修复提案" in rewritten
    assert "不执行 Apply" in rewritten
    assert "Apply" in rewritten
    assert "AttributeError" in rewritten


def test_how_to_fix_reads_l2_prior_traceback_when_l1_empty() -> None:
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    session = ConversationSession(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
        recent_turns=(),
        summary=ConversationSummary(
            project=project,
            active_skill=ProjectSkill.INCIDENT_DIAGNOSIS.value,
            active_topic={"prior_traceback": _TRACEBACK},
            updated_at=datetime.now(timezone.utc),
        ),
    )
    rewritten = FollowupRewriter().rewrite("怎么修？", session)
    assert rewritten is not None
    assert "AttributeError" in rewritten
    assert "order_service.py" in rewritten


def test_for_product_business_summary() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite("发给产品看", _session(skill="incident_diagnosis"))
    assert rewritten is not None
    assert "业务影响摘要" in rewritten
    assert "非技术" in rewritten or "产品" in rewritten
    assert rewriter.rewrite("给产品看的版本", _session(skill="architecture")) is not None


def test_expand_technical_details_followup() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite("展开技术细节", _session(skill="architecture"))
    assert rewritten is not None
    assert "技术细节" in rewritten
    assert "证据" in rewritten


def test_view_evidence_and_role_followups() -> None:
    rewriter = FollowupRewriter()
    evidence = rewriter.rewrite("查看证据", _session(skill="architecture"))
    assert evidence is not None
    assert "证据" in evidence
    assert rewriter.rewrite("给开发看的版本", _session(skill="architecture")) is not None
    assert rewriter.rewrite("给测试看的版本", _session(skill="incident_diagnosis")) is not None
    assert rewriter.rewrite("给新人看的版本", _session(skill="project_knowledge")) is not None


def test_what_missing_knowledge_gap() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite("还缺什么？", _session(skill="project_knowledge"))
    assert rewritten is not None
    assert "KnowledgeGapReport" in rewritten


def test_sync_status_followup() -> None:
    rewriter = FollowupRewriter()
    rewritten = rewriter.rewrite("同步了吗？", _session(skill=None))
    assert rewritten is not None
    assert "同步状态" in rewritten
    assert "revision" in rewritten
    assert rewriter.is_sync_status_followup("同步了吗？")


def test_non_followup_returns_none() -> None:
    rewriter = FollowupRewriter()
    assert rewriter.rewrite("这个项目的架构是什么？", _session(skill=None)) is None
