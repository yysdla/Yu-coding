"""Feishu intent gate: project-related vs general collaboration.

Does not query EvidenceIndex / graph / ops stores.
Only classifies message text (+ optional session) for routing.
"""

from __future__ import annotations

import re

from project_lens.context.retrieval.exact import parse_traceback
from project_lens.domain.conversation import ConversationSession
from project_lens.integrations.feishu.commands import (
    FeishuProjectCommand,
    classify_feishu_command,
)
from project_lens.workflow.followup import FollowupRewriter
from project_lens.workflow.skills import ProjectSkill, classify_project_question

_rewriter = FollowupRewriter()

_MENTION_TOKEN = re.compile(r"@_user_\d+")
_CODE_IDENT = re.compile(
    r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b|[A-Z][a-zA-Z0-9]+[A-Z][a-zA-Z0-9]*"
)

_CHITCHAT_TERMS = (
    "你好",
    "您好",
    "嗨",
    "hi",
    "hello",
    "在吗",
    "在不在",
    "谢谢",
    "感谢",
    "多谢",
    "早上好",
    "中午好",
    "下午好",
    "晚上好",
    "午安",
    "晚安",
    "哈哈哈",
    "哈哈",
    "吃什么",
    "午饭",
    "晚饭",
    "天气",
    "周末愉快",
    "打个招呼",
)

_BOT_META_MODEL_TERMS = (
    "什么模型",
    "用的什么模型",
    "用的是什么模型",
    "哪个模型",
    "哪款模型",
    "模型是什么",
    "what model",
    "which model",
    "你用什么llm",
    "用的什么llm",
)

_BOT_META_ABOUT_TERMS = (
    "你是谁",
    "你是什么",
    "你能做什么",
    "你会什么",
    "你是干什么的",
    "介绍一下你自己",
    "介绍下你自己",
    "what can you do",
    "who are you",
    "你有什么能力",
)

_PROJECT_SIGNAL_TERMS = (
    "项目",
    "服务",
    "模块",
    "架构",
    "依赖",
    "入口",
    "接口",
    "文档",
    "知识库",
    "负责人",
    "owner",
    "变更",
    "发布",
    "版本",
    "上线",
    "commit",
    "故障",
    "事故",
    "报错",
    "异常",
    "错误",
    "影响范围",
    "代码",
    "函数",
    "方法",
    "实现",
    "同步",
    "readme",
    "adr",
    "runbook",
    "traceback",
    "exception",
    "incident",
    "architecture",
    "dependency",
    "release",
    "deploy",
    "rollback",
    "pr",
    "pull request",
)


def strip_feishu_mentions(text: str) -> str:
    """Remove Feishu @_user_N mention tokens from message text."""

    cleaned = _MENTION_TOKEN.sub(" ", text)
    return " ".join(cleaned.split()).strip()


def is_bot_meta_question(text: str) -> bool:
    """True for questions about ProjectLens itself (model / capabilities)."""

    normalized = _normalize(strip_feishu_mentions(text))
    if not normalized:
        return False
    if any(_normalize(term) in normalized for term in _BOT_META_MODEL_TERMS):
        return True
    return any(_normalize(term) in normalized for term in _BOT_META_ABOUT_TERMS)


def is_model_meta_question(text: str) -> bool:
    normalized = _normalize(strip_feishu_mentions(text))
    return any(_normalize(term) in normalized for term in _BOT_META_MODEL_TERMS)


def is_project_related_message(
    text: str,
    *,
    session: ConversationSession | None = None,
) -> bool:
    """True when the message should enter the project AgentRun path."""

    stripped = strip_feishu_mentions(text.strip())
    if not stripped:
        return False

    # Bot/meta questions never force a project Skill analysis.
    if is_bot_meta_question(stripped):
        return False

    if _rewriter.rewrite(stripped, session) is not None:
        return True
    if classify_feishu_command(stripped) != FeishuProjectCommand.FREE_QUESTION:
        return True

    frames, exception = parse_traceback(stripped)
    if frames or exception:
        return True

    if _has_project_signal(stripped):
        return True

    # Mid-conversation ambiguous text stays on the project path,
    # but never overrides chitchat / bot meta (already handled above).
    if (
        session is not None
        and session.recent_turns
        and session.summary.active_skill
        and not _is_explicit_chitchat(stripped)
    ):
        return True

    if _is_explicit_chitchat(stripped):
        return False

    return False


def _has_project_signal(text: str) -> bool:
    normalized = _normalize(text)
    if any(_normalize(term) in normalized for term in _PROJECT_SIGNAL_TERMS):
        return True
    if _CODE_IDENT.search(text):
        return True
    skill = classify_project_question(text)
    return skill != ProjectSkill.PROJECT_KNOWLEDGE


def _is_explicit_chitchat(text: str) -> bool:
    normalized = _normalize(text)
    if any(_normalize(term) in normalized for term in _CHITCHAT_TERMS):
        return True
    return len(normalized) <= 4 and not any(ch.isalnum() for ch in text if ord(ch) < 128)


def _normalize(text: str) -> str:
    return "".join(text.casefold().strip().split())
