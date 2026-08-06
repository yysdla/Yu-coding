"""AnswerAudience: who is reading the Feishu answer (presentation only).

Does not invent facts. Consumes AgentRun + ProjectAnswer only.
"""

from __future__ import annotations

from enum import StrEnum


class AnswerAudience(StrEnum):
    TEAM = "team"
    TECHNICAL = "technical"
    BUSINESS = "business"
    QA = "qa"
    MANAGER = "manager"
    ONBOARDING = "onboarding"
    EVIDENCE = "evidence"
    DEBUG = "debug"


_AUDIENCE_TITLES: dict[AnswerAudience, str] = {
    AnswerAudience.TEAM: "团队协作视图",
    AnswerAudience.TECHNICAL: "技术视图",
    AnswerAudience.BUSINESS: "业务/产品视图",
    AnswerAudience.QA: "测试视图",
    AnswerAudience.MANAGER: "管理/进度视图",
    AnswerAudience.ONBOARDING: "新人理解视图",
    AnswerAudience.EVIDENCE: "证据视图",
    AnswerAudience.DEBUG: "调试视图",
}

# Phrases that switch audience without re-asking the project question.
_SWITCH_MAP: tuple[tuple[AnswerAudience, tuple[str, ...]], ...] = (
    (
        AnswerAudience.TECHNICAL,
        (
            "给技术看的版本",
            "给开发看的版本",
            "给开发看",
            "技术视角",
            "开发视角",
            "展开技术细节",
        ),
    ),
    (
        AnswerAudience.BUSINESS,
        (
            "给产品/业务看的版本",
            "给产品看的版本",
            "给业务看的版本",
            "给产品看",
            "产品视角",
            "业务摘要",
            "发给产品看",
        ),
    ),
    (
        AnswerAudience.QA,
        (
            "给测试看的版本",
            "给测试看",
            "测试视角",
        ),
    ),
    (
        AnswerAudience.MANAGER,
        (
            "给管理看的版本",
            "进度视图",
            "管理视角",
        ),
    ),
    (
        AnswerAudience.ONBOARDING,
        (
            "给新人看的版本",
            "给新人看",
            "新人视角",
        ),
    ),
    (
        AnswerAudience.EVIDENCE,
        (
            "查看证据",
            "证据详情",
            "看证据",
            "查看证据来源",
        ),
    ),
    (
        AnswerAudience.DEBUG,
        (
            "查看调试信息",
            "调试信息",
            "debug视图",
        ),
    ),
    (
        AnswerAudience.TEAM,
        (
            "回到团队视图",
            "团队协作视图",
            "团队视角",
        ),
    ),
)


def audience_label(audience: AnswerAudience) -> str:
    return _AUDIENCE_TITLES.get(audience, "团队协作视图")


def select_answer_audience(
    question: str = "",
    *,
    explicit: AnswerAudience | None = None,
) -> AnswerAudience:
    """Resolve reading audience. Default is TEAM collaboration view."""

    if explicit is not None:
        return explicit
    switched = detect_audience_switch(question)
    return switched or AnswerAudience.TEAM


def detect_audience_switch(text: str) -> AnswerAudience | None:
    """Return audience when the message is a pure RoleView switch request.

    Avoid matching long project questions that merely mention a role word.
    """

    normalized = "".join(text.casefold().strip().split())
    if not normalized:
        return None
    for audience, phrases in _SWITCH_MAP:
        for phrase in phrases:
            phrase_n = "".join(phrase.casefold().split())
            if not phrase_n:
                continue
            if normalized == phrase_n:
                return audience
            # Short button-like messages only (e.g. 「请给技术看的版本」).
            if phrase_n in normalized and len(normalized) <= len(phrase_n) + 8:
                return audience
    return None
