"""Rule-based follow-up question rewriting for multi-turn Feishu sessions."""

from __future__ import annotations

from project_lens.context.retrieval.exact import parse_traceback
from project_lens.domain.conversation import ConversationSession
from project_lens.workflow.skills import ProjectSkill


class FollowupRewriter:
    """Deterministic rewriter. Does not call an LLM or touch EvidenceIndex."""

    def rewrite(
        self,
        text: str,
        session: ConversationSession | None,
    ) -> str | None:
        """Return a rewritten question when text is a known follow-up, else None."""

        normalized = _normalize(text)
        if not normalized:
            return None
        skill = _active_skill(session)
        if _matches(normalized, _WHO_CHANGED):
            return _rewrite_who_changed(skill)
        if _matches(normalized, _IMPACT_WHERE):
            return _rewrite_impact_where(skill)
        if _matches(normalized, _HOW_TO_FIX):
            return _rewrite_how_to_fix(skill, session)
        if _matches(normalized, _FOR_PRODUCT):
            return (
                "基于上一轮已验证结论，生成产品/非技术人员可读的业务影响摘要、"
                "当前状态、未知项和下一步动作。"
            )
        if _matches(normalized, _FOR_DEV):
            return (
                "基于上一轮已验证结论，生成开发可读版本：关键入口、依赖、证据来源、"
                "风险项和需要验证的回归点。"
            )
        if _matches(normalized, _FOR_QA):
            return (
                "基于上一轮已验证结论，生成测试可读版本：影响范围、建议验证场景、"
                "未知项和回归检查清单。"
            )
        if _matches(normalized, _FOR_NEWCOMER):
            return (
                "基于上一轮已验证结论，生成新人可读版本：项目定位、核心服务、"
                "关键入口、资料缺口和推荐下一步阅读。"
            )
        if _matches(normalized, _TECH_DETAIL):
            return (
                "基于上一轮已验证结论，展开技术细节：证据来源、关系路径、关键入口、"
                "风险项和下一步验证动作。"
            )
        if _matches(normalized, _VIEW_EVIDENCE):
            return (
                "基于上一轮已验证结论，列出参考来源与证据要点：文档路径、"
                "commit / 任务 / 关系路径，并标出仍缺的证据。"
            )
        if _matches(normalized, _WHAT_MISSING):
            return (
                "基于当前项目和上一轮上下文，生成 KnowledgeGapReport，"
                "说明知识库缺失资料和建议补充内容。"
            )
        if _matches(normalized, _SYNC_STATUS):
            return (
                "查询当前项目 Feishu 文档同步状态、最近 revision、失败 token "
                "和知识库最近更新时间。"
            )
        return None

    def is_sync_status_followup(self, text: str) -> bool:
        return _matches(_normalize(text), _SYNC_STATUS)


_WHO_CHANGED = ("那是谁改的", "谁改的", "谁改的？", "谁提交的")
_IMPACT_WHERE = ("影响哪里", "影响哪些", "影响面", "影响范围呢")
_HOW_TO_FIX = ("怎么修", "如何修复", "怎么修复", "怎么改")
_FOR_PRODUCT = (
    "发给产品看",
    "给产品看",
    "产品视角",
    "业务摘要",
    "给产品看的版本",
    "给产品/业务看的版本",
    "给业务看的版本",
)
_FOR_DEV = ("给开发看的版本", "给开发看", "开发视角", "给技术看的版本")
_FOR_QA = ("给测试看的版本", "给测试看", "测试视角")
_FOR_NEWCOMER = ("给新人看的版本", "给新人看", "新人视角")
_TECH_DETAIL = ("展开技术细节", "技术细节", "技术视角")
_VIEW_EVIDENCE = ("查看证据", "证据详情", "看证据", "查看证据来源")
_WHAT_MISSING = ("还缺什么", "缺什么资料", "还缺哪些")
_SYNC_STATUS = ("同步了吗", "同步了没", "文档同步了吗", "知识库同步了吗")


def _active_skill(session: ConversationSession | None) -> str | None:
    if session is None:
        return None
    return session.summary.active_skill


def _rewrite_who_changed(skill: str | None) -> str:
    if skill == ProjectSkill.VERSION_CHANGE.value:
        return (
            "基于上一轮版本变更分析，查找相关 commit、任务、负责人和变更影响。"
        )
    if skill in {"project_investigation", ProjectSkill.CODE_EXPLANATION.value}:
        return (
            "基于上一轮项目调查，查找相关 commit、任务、负责人和变更影响；"
            "优先复用已钉住的文件路径与符号。"
        )
    return (
        "基于上一轮故障诊断，查找相关 commit、任务、负责人和变更影响。"
    )


def _rewrite_impact_where(skill: str | None) -> str:
    if skill == ProjectSkill.INCIDENT_DIAGNOSIS.value:
        return (
            "基于上一轮故障诊断，说明受影响服务、入口、风险项和需要回归的场景。"
        )
    if skill == "project_investigation":
        return (
            "基于上一轮项目调查，说明受影响服务、入口、风险项和需要回归的场景；"
            "优先复用已钉住的文件路径。"
        )
    return (
        "基于上一轮版本变更分析，说明受影响服务、入口、风险项和需要回归的场景。"
    )


def _rewrite_how_to_fix(
    skill: str | None,
    session: ConversationSession | None,
) -> str:
    prefix = "基于上一轮故障或代码分析"
    if skill == ProjectSkill.CODE_EXPLANATION.value:
        prefix = "基于上一轮代码分析"
    elif skill == ProjectSkill.INCIDENT_DIAGNOSIS.value:
        prefix = "基于上一轮故障诊断"
    elif skill == "project_investigation":
        prefix = "基于上一轮项目调查"
    rewritten = (
        f"{prefix}，生成只读 Engineering 修复提案，展示 patch plan、diff、"
        "测试结果和审批需求，不执行 Apply。"
    )
    # Keep prior traceback pinned so Engineering bridge can still Validate read-only.
    traceback = _prior_traceback(session)
    if traceback:
        return f"{rewritten}\n\n{traceback}"
    return rewritten


def _prior_traceback(session: ConversationSession | None) -> str | None:
    if session is None:
        return None
    for turn in reversed(session.recent_turns):
        for candidate in (turn.text, turn.rewritten_question or ""):
            frames, exception = parse_traceback(candidate)
            if frames or exception:
                return candidate
    # L2 pin: survives L1 eviction and process restart via durable session JSON.
    pinned = (session.summary.active_topic or {}).get("prior_traceback")
    if pinned:
        frames, exception = parse_traceback(pinned)
        if frames or exception:
            return pinned
    return None


def _normalize(text: str) -> str:
    return "".join(text.casefold().strip().split())


def _matches(normalized: str, terms: tuple[str, ...]) -> bool:
    return any(_normalize(term) in normalized for term in terms)
