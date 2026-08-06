"""Read-agent free-question eval / replay sets (not Skill-routed).

These questions intentionally avoid pre-baked skill buckets so we can verify
ProjectInvestigationAgent + code navigation tools.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadAgentFreeQuestion:
    name: str
    question: str
    description: str
    # Soft expectations for stub planner / live loop.
    expect_tools_any_of: tuple[str, ...] = ()
    expect_file_read: bool = False
    expect_unknown_when_missing_author: bool = False


READ_AGENT_FREE_QUESTIONS: tuple[ReadAgentFreeQuestion, ...] = (
    ReadAgentFreeQuestion(
        name="coupon_check_in_create_order",
        question="order_service.py 里 create_order 为什么要检查 coupon？",
        description="Path-mentioned free question; should search + read file",
        expect_tools_any_of=("search_context", "read_project_file"),
        expect_file_read=True,
    ),
    ReadAgentFreeQuestion(
        name="order_creation_entrypoint",
        question="这个项目里哪个文件最像订单创建入口？",
        description="Entry navigation via list/grep",
        expect_tools_any_of=(
            "search_context",
            "list_project_files",
            "grep_project_code",
            "search_project_code",
        ),
    ),
    ReadAgentFreeQuestion(
        name="docs_vs_code_entry",
        question="README/架构文档和代码入口是否对应？",
        description="Doc vs code correspondence",
        expect_tools_any_of=(
            "search_context",
            "read_project_file",
            "grep_project_code",
        ),
        expect_file_read=True,
    ),
    ReadAgentFreeQuestion(
        name="who_changed_order_creation",
        question="最近谁改过订单创建相关代码？",
        description="Author question; may unknown if commit authors missing",
        expect_tools_any_of=("search_context", "search_project_code", "grep_project_code"),
        expect_unknown_when_missing_author=True,
    ),
)


def list_read_agent_free_questions() -> tuple[ReadAgentFreeQuestion, ...]:
    return READ_AGENT_FREE_QUESTIONS


def get_read_agent_free_question(name: str) -> ReadAgentFreeQuestion:
    for item in READ_AGENT_FREE_QUESTIONS:
        if item.name == name:
            return item
    raise KeyError(f"unknown read_agent free question: {name}")
