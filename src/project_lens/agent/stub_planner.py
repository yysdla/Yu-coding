"""Stub investigation planner: chooses read tools without Skill routing.

This stands in for a live LLM tool-calling model when AGENT_MODE=read_agent
and live LLM is off. It still drives an AgentLoop (tool calls → results → draft),
unlike the old classify_project_question → deterministic AnalysisAgent path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from project_lens.agent.draft import AnswerDraft, DraftFact
from project_lens.runtime.types import Message, ModelResponse, Role, ToolCall, ToolDefinition

_PATH_RE = re.compile(
    r"(?P<path>(?:src/|tests/|knowledge/)?[\w./-]+\.(?:py|md|json|txt|yml|yaml))",
    re.IGNORECASE,
)

_ENTRY_MARKERS = ("哪个文件", "入口", "entrypoint", "entry point", "最像")
_DOC_MARKERS = ("readme", "架构文档", "架构", "是否对应", "文档和代码")
_WHO_MARKERS = ("谁改过", "谁改的", "最近谁", "谁提交")
_CODE_MARKERS = ("create_order", "coupon", "订单创建", "order_service")


class InvestigationStubProvider:
    """Heuristic read-agent planner for tests and offline harness.

    Not a Skill classifier: always investigates via tools, then drafts citations.
    """

    def __init__(
        self,
        *,
        question: str,
        prior_paths: tuple[str, ...] = (),
        prior_hints: tuple[str, ...] = (),
    ) -> None:
        self._question = question
        self._prior_paths = tuple(prior_paths)
        self._prior_hints = tuple(prior_hints)
        self._called: set[str] = set()

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        available = {item.name for item in tools}
        path = _extract_path(self._question)
        if path is None and _looks_like_followup(self._question):
            path = _first_prior_path(self._prior_paths)
        q = self._question.casefold()
        search_query = _search_query(self._question, self._prior_hints)

        if "search_context" in available and "search_context" not in self._called:
            self._called.add("search_context")
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        id=f"call-{uuid4().hex[:8]}",
                        name="search_context",
                        arguments={"query": search_query[:500], "limit": 8},
                    ),
                ),
                usage={"total_tokens": 12},
            )

        if (
            path
            and "read_project_file" in available
            and "read_project_file" not in self._called
        ):
            self._called.add("read_project_file")
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        id=f"call-{uuid4().hex[:8]}",
                        name="read_project_file",
                        arguments={"path": path},
                    ),
                ),
                usage={"total_tokens": 10},
            )

        # Follow-up with prior paths but no explicit filename in the question.
        if (
            self._prior_paths
            and "read_project_file" in available
            and "read_project_file" not in self._called
            and _looks_like_followup(self._question)
        ):
            self._called.add("read_project_file")
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        id=f"call-{uuid4().hex[:8]}",
                        name="read_project_file",
                        arguments={"path": self._prior_paths[0]},
                    ),
                ),
                usage={"total_tokens": 10},
            )

        if (
            any(marker in self._question for marker in _ENTRY_MARKERS)
            or "entrypoint" in q
        ):
            if (
                "list_project_files" in available
                and "list_project_files" not in self._called
            ):
                self._called.add("list_project_files")
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{uuid4().hex[:8]}",
                            name="list_project_files",
                            arguments={"prefix": "src/", "limit": 40},
                        ),
                    ),
                    usage={"total_tokens": 10},
                )
            if (
                "grep_project_code" in available
                and "grep_project_code" not in self._called
            ):
                self._called.add("grep_project_code")
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{uuid4().hex[:8]}",
                            name="grep_project_code",
                            arguments={
                                "pattern": r"create_order|POST /orders|def create_",
                                "prefix": "src/",
                                "limit": 20,
                            },
                        ),
                    ),
                    usage={"total_tokens": 10},
                )

        if any(marker.casefold() in q for marker in _DOC_MARKERS):
            if (
                "read_project_file" in available
                and "read_project_file" not in self._called
            ):
                self._called.add("read_project_file")
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{uuid4().hex[:8]}",
                            name="read_project_file",
                            arguments={"path": "knowledge/architecture.md"},
                        ),
                    ),
                    usage={"total_tokens": 10},
                )
            if (
                "grep_project_code" in available
                and "grep_project_code" not in self._called
            ):
                self._called.add("grep_project_code")
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{uuid4().hex[:8]}",
                            name="grep_project_code",
                            arguments={
                                "pattern": r"route:|POST /|create_order",
                                "prefix": "src/",
                                "limit": 20,
                            },
                        ),
                    ),
                    usage={"total_tokens": 10},
                )

        if any(marker in self._question for marker in _WHO_MARKERS):
            # search_context already ran; try code nav then stop with cited unknowns.
            if (
                "search_project_code" in available
                and "search_project_code" not in self._called
            ):
                self._called.add("search_project_code")
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{uuid4().hex[:8]}",
                            name="search_project_code",
                            arguments={
                                "pattern": r"create_order|order_service|coupon",
                                "prefix": "src/",
                                "limit": 15,
                            },
                        ),
                    ),
                    usage={"total_tokens": 10},
                )

        if any(marker.casefold() in q for marker in _CODE_MARKERS):
            if (
                "grep_project_code" in available
                and "grep_project_code" not in self._called
                and "read_project_file" not in self._called
            ):
                self._called.add("grep_project_code")
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{uuid4().hex[:8]}",
                            name="grep_project_code",
                            arguments={
                                "pattern": r"coupon|create_order",
                                "prefix": "src/",
                                "limit": 20,
                            },
                        ),
                    ),
                    usage={"total_tokens": 10},
                )
            hit_path, hit_line = _first_grep_hit(messages)
            if (
                hit_path
                and "read_project_file_range" in available
                and "read_project_file_range" not in self._called
            ):
                self._called.add("read_project_file_range")
                start = max(1, (hit_line or 1) - 5)
                end = start + 40
                return ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{uuid4().hex[:8]}",
                            name="read_project_file_range",
                            arguments={
                                "path": hit_path,
                                "start_line": start,
                                "end_line": end,
                            },
                        ),
                    ),
                    usage={"total_tokens": 10},
                )

        draft = _synthesize_draft(self._question, messages, tools_used=sorted(self._called))
        return ModelResponse(
            content=draft.model_dump_json(),
            usage={"total_tokens": 20},
        )


def _extract_path(question: str) -> str | None:
    match = _PATH_RE.search(question.replace("\\", "/"))
    if not match:
        # Bare filename like order_service.py → src/order_service.py
        bare = re.search(r"\b([\w-]+\.py)\b", question)
        if bare:
            return f"src/{bare.group(1)}"
        return None
    path = match.group("path").lstrip("./")
    if "/" not in path and path.endswith(".py"):
        return f"src/{path}"
    return path


def _first_prior_path(prior_paths: tuple[str, ...]) -> str | None:
    for path in prior_paths:
        cleaned = path.replace("\\", "/").lstrip("./")
        if cleaned.startswith(("src/", "tests/", "knowledge/")):
            return cleaned
        if cleaned.endswith((".py", ".md", ".json")):
            return f"src/{cleaned}" if "/" not in cleaned else cleaned
    return None


def _looks_like_followup(question: str) -> bool:
    if "基于上一轮" in question or "上一轮" in question:
        return True
    return any(marker in question for marker in _WHO_MARKERS)


def _search_query(question: str, prior_hints: tuple[str, ...]) -> str:
    if not prior_hints:
        return question
    if _looks_like_followup(question):
        hint_blob = " ".join(prior_hints[:6])
        return f"{question}\nprior_context: {hint_blob}"
    return question


def _first_grep_hit(messages: Sequence[Message]) -> tuple[str | None, int | None]:
    for message in messages:
        if message.role != Role.TOOL or not message.content:
            continue
        try:
            payload: dict[str, Any] = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        hits = payload.get("hits")
        if not isinstance(hits, list) or not hits:
            continue
        first = hits[0]
        if not isinstance(first, dict):
            continue
        path = first.get("path")
        if not path:
            continue
        line = first.get("line")
        return str(path), int(line) if line is not None else None
    return None, None


def _synthesize_draft(
    question: str,
    messages: Sequence[Message],
    *,
    tools_used: list[str],
) -> AnswerDraft:
    citations: list[str] = []
    snippets: list[str] = []
    for message in messages:
        if message.role != Role.TOOL or not message.content:
            continue
        try:
            payload: dict[str, Any] = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if "hits" in payload:
            for hit in payload.get("hits") or []:
                if not isinstance(hit, dict):
                    continue
                eid = hit.get("evidence_id")
                if eid:
                    citations.append(str(eid))
                snippet = str(hit.get("snippet") or hit.get("text") or "").strip()
                source = str(hit.get("source_id") or hit.get("path") or "")
                if snippet:
                    snippets.append(f"{source}: {snippet[:160]}")
        if payload.get("evidence_id"):
            citations.append(str(payload["evidence_id"]))
            path = payload.get("path") or payload.get("prefix") or payload.get("pattern")
            content = str(payload.get("content") or "")[:160]
            if path or content:
                snippets.append(f"{path or 'tool'}: {content or str(payload.get('files') or '')[:160]}")
            for hit in payload.get("hits") or []:
                if isinstance(hit, dict) and hit.get("text"):
                    snippets.append(
                        f"{hit.get('path')}:{hit.get('line')}: {str(hit.get('text'))[:120]}"
                    )

    unique_citations = list(dict.fromkeys(citations))
    facts: list[DraftFact] = []
    if unique_citations and snippets:
        facts.append(
            DraftFact(
                text=f"根据已读取资料：{snippets[0]}",
                citations=unique_citations[:4],
            )
        )
        if len(snippets) > 1:
            facts.append(
                DraftFact(
                    text=f"补充来源：{snippets[1]}",
                    citations=unique_citations[:4],
                )
            )
    unknowns: list[str] = []
    next_actions: list[str] = []
    who_question = any(marker in question for marker in _WHO_MARKERS)
    if who_question:
        unknowns.append(
            "已查 search_context / 代码导航，但当前索引缺少可引用的作者/提交人 Evidence；"
            "需要补充 git blame / commit author 证据后再回答「谁改过」。"
        )
        next_actions.append("接入或补充 commit/author Evidence 后重试。")
    if not unique_citations:
        unknowns.append(
            f"已尝试工具 {', '.join(tools_used) or '（无）'}，但未获得可引用 Evidence。"
        )
        next_actions.append("补充相关代码/文档到项目知识库后重试。")
        next_actions.append("明确文件路径或符号名，便于 read_project_file / grep_project_code。")
        summary = (
            f"针对「{question[:80]}」已发起只读调查，但当前 ProjectSpace 内缺少可引用资料。"
        )
    else:
        summary = (
            f"针对「{question[:80]}」已通过只读工具调查，结论均引用工具返回的 Evidence。"
        )
        if who_question and not any("作者" in s or "commit" in s.casefold() for s in snippets):
            # Keep facts from code hits but make authorship an unknown.
            if not any("提交人" in item or "作者" in item for item in unknowns):
                unknowns.append(
                    "代码命中了订单创建相关文件，但缺少「最近修改人」的 commit Evidence。"
                )

    return AnswerDraft(
        facts=facts,
        inferences=[],
        unknowns=unknowns,
        next_actions=next_actions,
        business_summary=summary,
        technical_summary=(
            f"tools={tools_used}; citations={len(unique_citations)}"
            if tools_used
            else "no tools used"
        ),
        tools_used=tools_used,
        stop_reason="stop",
    )
