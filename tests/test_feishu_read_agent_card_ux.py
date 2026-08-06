"""read_agent free-question Feishu card UX: answer first, debug last."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from project_lens.domain.models import (
    AgentRun,
    Claim,
    ClaimType,
    Evidence,
    EvidenceGrade,
    EvidenceType,
    ProjectAnswer,
    ProjectRef,
    SourceRef,
)
from project_lens.integrations.feishu.audiences import AnswerAudience
from project_lens.integrations.feishu.audit_summary import FeishuAuditSummary
from project_lens.integrations.feishu.cards import render_answer_card, render_progress_text
from project_lens.integrations.feishu.views import (
    AnswerView,
    answer_view_title,
    select_answer_view,
    select_investigation_answer_view,
)


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _run(question: str) -> AgentRun:
    return AgentRun(
        project=_project(),
        user_id="u1",
        channel_id="chat-1",
        question=question,
    )


def _evidence(*, path: str, evidence_id=None) -> Evidence:
    return Evidence(
        id=evidence_id or uuid4(),
        type=EvidenceType.CODE,
        project=_project(),
        source=SourceRef(system="repository", source_id=path),
        content="coupon_id = request.coupon.id",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
        metadata={"path": path, "tool": "read_project_file"},
    )


def _investigation_answer(
    *,
    question_hint: str = "coupon",
    evidence: tuple[Evidence, ...] = (),
    unknowns: tuple[str, ...] = (),
) -> ProjectAnswer:
    evid = evidence or (
        _evidence(path="src/order_service.py"),
        _evidence(path="knowledge/architecture.md"),
        _evidence(path="src/order_service.py"),
        _evidence(path="knowledge/runbook.md"),
        _evidence(path="knowledge/releases.json"),
        _evidence(path="tests/test_order_service.py"),
    )
    claims = (
        Claim(
            text="create_order 在校验 coupon 后创建订单，避免无效优惠进入支付。",
            type=ClaimType.FACT,
            evidence_ids=(evid[0].id,),
            grade=EvidenceGrade.B,
        ),
        Claim(
            text="该入口影响支付下单用户路径。",
            type=ClaimType.FACT,
            evidence_ids=(evid[1].id,),
            grade=EvidenceGrade.B,
        ),
    )
    return ProjectAnswer(
        project=_project(),
        status="identified",
        skill="project_investigation",
        confidence=0.7,
        business_summary=f"针对「{question_hint}」：下单前会校验 coupon，结论来自项目资料。",
        technical_summary="tools=search_context,read_project_file; citations=2",
        claims=claims,
        evidence=evid,
        unknowns=unknowns or ("优惠券服务超时链路缺少近期监控证据。",),
    )


def _joined(card: dict[str, object]) -> str:
    return "\n".join(
        str(item.get("content") or "")
        for item in card.get("elements", [])  # type: ignore[union-attr]
        if isinstance(item, dict)
    )


def _pre_debug(joined: str) -> str:
    if "**调试信息**" not in joined:
        return joined
    return joined.split("**调试信息**", 1)[0]


def test_investigation_view_picks_human_titles_not_generic_survey() -> None:
    coupon_q = "order_service.py 里 create_order 为什么要检查 coupon？"
    assert (
        select_investigation_answer_view(
            _run(coupon_q),
            _investigation_answer(question_hint=coupon_q),
        )
        == AnswerView.REASON_ANALYSIS
    )
    assert answer_view_title(AnswerView.REASON_ANALYSIS) == "ProjectLens 原因分析"
    assert "项目调查" not in answer_view_title(AnswerView.REASON_ANALYSIS)

    entry_q = "这个项目里哪个文件最像订单创建入口？"
    assert (
        select_answer_view(
            _run(entry_q),
            _investigation_answer(question_hint=entry_q),
        )
        == AnswerView.CODE_EXPLANATION
    )
    assert answer_view_title(AnswerView.CODE_EXPLANATION) == "ProjectLens 代码解释"

    arch_q = "README/架构文档和代码入口是否对应？"
    assert (
        select_answer_view(
            _run(arch_q),
            _investigation_answer(question_hint=arch_q),
        )
        == AnswerView.ARCHITECTURE_EXPLAIN
    )
    assert answer_view_title(AnswerView.ARCHITECTURE_EXPLAIN) == "ProjectLens 架构说明"

    who_q = "最近谁改过订单创建相关代码？"
    assert (
        select_answer_view(
            _run(who_q),
            _investigation_answer(question_hint=who_q),
        )
        == AnswerView.CHANGE_IMPACT
    )


def test_free_question_card_first_screen_is_answer_not_sources_or_skillguide() -> None:
    question = "order_service.py 里 create_order 为什么要检查 coupon？"
    answer = _investigation_answer(question_hint=question)
    audit = FeishuAuditSummary(
        skill="project_investigation",
        agent_mode="read_agent",
        provider="stub_planner",
        skill_guide="free_question",
        read_tool_names="search_context, read_project_file",
        evidence_ids_preview=", ".join(str(item.id) for item in answer.evidence[:3]),
        technical_summary_preview=answer.technical_summary,
        trace_id=str(uuid4()),
        allow_apply="False",
    )
    card = render_answer_card(_run(question), answer, audit_summary=audit)
    assert card["header"]["title"]["content"] == "ProjectLens 原因分析"
    joined = _joined(card)
    pre = _pre_debug(joined)

    # Default first screen: human answer sections.
    for section in (
        "**一句话结论**",
        "**已确认**",
        "**当前未知**",
        "**对团队意味着什么**",
        "**建议下一步**",
        "**来源摘要**",
    ):
        assert section in pre, section

    assert "已基于" in pre and "条项目资料" in pre
    assert "项目调查" not in pre
    assert "SkillGuide" not in pre
    assert "内部路由" not in pre
    assert "free_question" not in pre
    assert "search_context" not in pre
    assert "read_project_file" not in pre
    assert "stub_planner" not in pre
    assert "tools=search_context" not in pre
    assert "provider" not in pre.casefold() or "provider:" not in pre
    assert "trace_id" not in pre
    assert "**本次参考来源**" not in pre
    assert not pre.strip().startswith("**参考来源**")

    # Evidence IDs / SkillGuide / tools / provider only in debug zone.
    assert "**调试信息**" in joined
    assert joined.index("**一句话结论**") < joined.index("**调试信息**")
    debug = joined[joined.index("**调试信息**") :]
    assert "skill_guide: free_question" in debug
    assert "read_tools:" in debug
    assert "evidence_ids:" in debug
    assert "内部路由：project_investigation" in debug
    assert "provider: stub_planner" in debug
    assert "trace_id:" in debug
    for item in answer.evidence:
        assert str(item.id) not in pre


def test_evidence_and_debug_views_keep_fuller_source_and_audit() -> None:
    question = "order_service.py 里 create_order 为什么要检查 coupon？"
    answer = _investigation_answer(question_hint=question)
    run = _run(question)

    evidence_card = render_answer_card(run, answer, audience=AnswerAudience.EVIDENCE)
    evid_text = _joined(evidence_card)
    assert "**本次参考来源**" in evid_text
    assert "src/order_service.py" in evid_text or "architecture.md" in evid_text
    assert "证据视图" in evid_text

    debug_card = render_answer_card(run, answer, audience=AnswerAudience.DEBUG)
    debug_text = _joined(debug_card)
    assert "调试视图" in debug_text or "调试摘要" in debug_text
    assert "skill: project_investigation" in debug_text
    assert "trace_id:" in debug_text
    assert "evidence_ids:" in debug_text


def test_role_view_replay_reuses_same_project_answer_facts() -> None:
    answer = _investigation_answer()
    run = _run("create_order 为什么要检查 coupon？")
    team = _joined(render_answer_card(run, answer, audience=AnswerAudience.TEAM))
    tech = _joined(render_answer_card(run, answer, audience=AnswerAudience.TECHNICAL))
    business = _joined(render_answer_card(run, answer, audience=AnswerAudience.BUSINESS))

    for text in (team, tech, business):
        assert "校验 coupon" in text or "coupon" in text.casefold()
        assert "BillingGateway" not in text
        assert "全新编造的事实" not in text


def test_progress_text_has_no_run_id() -> None:
    text = render_progress_text("阅读项目资料", _run("介绍一下这个项目"))
    assert "run_id" not in text
    assert "阅读项目资料" in text


def test_insufficient_investigation_card_explains_search_gap_not_zero_percent() -> None:
    answer = ProjectAnswer(
        project=_project(),
        status="unknown",
        skill="project_investigation",
        confidence=0.0,
        business_summary="针对该问题已发起只读调查，但当前 ProjectSpace 内缺少可引用资料。",
        technical_summary="tools=search_context,grep_project_code; citations=0",
        claims=(),
        evidence=(),
        unknowns=(
            "已尝试检索项目资料，但未获得可引用来源。",
            "需要补充 git blame / commit author 证据后再回答「谁改过」。",
        ),
    )
    card = render_answer_card(
        _run("最近谁改过订单创建相关代码？"),
        answer,
    )
    joined = _joined(card)
    pre = _pre_debug(joined)
    assert "置信度 0%" not in joined
    assert "我查了什么" in joined
    assert "没找到什么" in joined or "还缺什么" in joined
    assert "SkillGuide" not in pre
    assert "项目调查" not in card["header"]["title"]["content"]
    assert card["header"]["title"]["content"] in {
        "ProjectLens 变更影响报告",
        "ProjectLens 项目回答",
    }
