"""Feishu -> Hermes AIAgent project tool-loop bridge.

Natural-language project questions optionally enter a real Hermes LLM/tool
loop via ``HermesLoopRunner``. Tools are only the formal projectlens_* set;
execution goes through ProjectLens Tool Envelope (RolePolicy / ChatVisibility
applied before tool work). No rule planner. No Hermes core changes. No direct
EvidenceIndex / GraphStore / filesystem access from this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_lens.application.project_agent_tools import ProjectAgentToolService
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.cards import FeishuCard, _markdown
from project_lens.integrations.feishu.hermes_loop_runner import (
    HermesAIAgentLoopRunner,
    HermesLoopRunner,
    HermesLoopRunResult,
    HermesLoopToolCall,
)
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.hermes_plugin.render import render_answer_envelope


@dataclass
class FeishuHermesToolLoopResult:
    ok: bool
    envelope: dict[str, Any]
    tool_names: tuple[str, ...] = ()
    output_markdown: str = ""


class ProjectLensToolClient:
    """In-process adapter around ProjectAgentToolService."""

    def __init__(self, service: ProjectAgentToolService) -> None:
        self._service = service

    def list_tools(self) -> dict[str, Any]:
        return self._service.list_tools()

    def call_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        from project_lens.application.project_agent_tools import ProjectAgentToolCallRequest

        request = ProjectAgentToolCallRequest(
            tool_name=str(payload["tool_name"]),
            project=ProjectRef.model_validate(payload["project"]),
            user_id=str(payload["user_id"]),
            chat_id=str(payload["chat_id"]),
            arguments=dict(payload.get("arguments") or {}),
        )
        return _run_async(self._service.call_tool(request))


class FeishuHermesToolLoopBridge:
    """Feishu bridge that delegates tool selection to a HermesLoopRunner."""

    def __init__(
        self,
        *,
        tool_service: ProjectAgentToolService,
        config: ProjectLensPluginConfig,
        loop_runner: HermesLoopRunner | None = None,
        hermes_repo: str = "",
        hermes_provider: str = "deepseek",
        hermes_model: str = "deepseek-v4-flash",
    ) -> None:
        self._tool_service = tool_service
        self._config = config
        self._client = ProjectLensToolClient(tool_service)
        self._loop_runner = loop_runner or HermesAIAgentLoopRunner(
            client=self._client,
            config=config,
            hermes_repo=hermes_repo,
            provider=hermes_provider,
            model=hermes_model,
        )
        self.last_tool_names: tuple[str, ...] = ()
        self.last_tool_calls: tuple[HermesLoopToolCall, ...] = ()
        self.last_envelope: dict[str, Any] | None = None
        self.last_error: str | None = None

    async def answer(
        self,
        *,
        project: ProjectRef,
        question: str,
        user_id: str,
        chat_id: str,
    ) -> FeishuHermesToolLoopResult:
        result = await _run_runner_async(
            self._loop_runner,
            question=question,
            project=project,
            user_id=user_id,
            chat_id=chat_id,
        )
        envelope = _build_answer_envelope(
            question=question,
            project=project,
            user_id=user_id,
            chat_id=chat_id,
            result=result,
        )
        self.last_tool_calls = result.tool_calls
        self.last_tool_names = tuple(call.name for call in result.tool_calls)
        self.last_envelope = envelope
        self.last_error = result.error
        markdown = render_answer_envelope(envelope, audience="team")
        return FeishuHermesToolLoopResult(
            ok=bool(result.ok),
            envelope=envelope,
            tool_names=self.last_tool_names,
            output_markdown=markdown,
        )


def render_hermes_tool_loop_card(markdown: str) -> dict[str, Any]:
    return FeishuCard(
        title="ProjectLens Hermes Tool Loop",
        elements=[_markdown(markdown)],
    ).to_payload()


def _build_answer_envelope(
    *,
    question: str,
    project: ProjectRef,
    user_id: str,
    chat_id: str,
    result: HermesLoopRunResult,
) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    unknowns: list[str] = []
    next_actions: list[dict[str, Any]] = []
    tool_calls_payload: list[dict[str, Any]] = []

    for call in result.tool_calls:
        tool_calls_payload.append(
            {
                "name": call.name,
                "arguments": call.arguments,
                "ok": call.envelope.get("ok"),
                "tool_result_id": call.envelope.get("tool_result_id"),
            }
        )
        citations.extend(_list_of_dicts(call.envelope.get("citations")))
        evidence_refs.extend(_list_of_dicts(call.envelope.get("evidence_refs")))
        if call.envelope.get("ok") is True:
            summary = str(call.envelope.get("summary") or "").strip()
            if summary:
                citation_ids = [
                    item.get("id")
                    for item in _list_of_dicts(call.envelope.get("citations"))
                    if item.get("id")
                ]
                facts.append({"text": summary, "citations": citation_ids})
            unknowns.extend(
                str(item) for item in call.envelope.get("unknowns") or [] if str(item).strip()
            )
        else:
            hint = str(call.envelope.get("agent_recovery_hint") or "").strip()
            if hint:
                next_actions.append({"title": hint, "requires_approval": False})
            unknowns.extend(
                str(item) for item in call.envelope.get("unknowns") or [] if str(item).strip()
            )

    if result.ok:
        answer_summary = (
            result.final_response.strip()
            or _compose_summary(question, result.tool_calls)
        )
    else:
        error = (result.error or "Hermes tool loop unavailable").strip()
        answer_summary = (
            f"Hermes LLM/tool loop 不可用，未能回答「{question}」。原因：{error}"
        )
        if error not in unknowns:
            unknowns.append(error)
        next_actions.append(
            {
                "title": "检查 Hermes 安装路径、模型凭证，或关闭 PROJECT_LENS_FEISHU_USE_HERMES_TOOL_LOOP 回退旧路径",
                "requires_approval": False,
            }
        )

    audit_ref = {
        "tool_names": [call.name for call in result.tool_calls],
        "allow_apply": False,
        "mode": "feishu_hermes_llm_tool_loop",
        "project_id": project.project_id,
        "tenant_id": project.tenant_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "hermes_ok": bool(result.ok),
    }
    if result.error:
        audit_ref["error"] = result.error

    # Prefer evidence_refs from tools; if only citations exist, mirror them.
    if not evidence_refs and citations:
        evidence_refs = list(citations)

    return {
        "ok": bool(result.ok),
        "answer_summary": answer_summary,
        "facts": facts,
        "inferences": [],
        "unknowns": unknowns,
        "next_actions": next_actions,
        "citations": citations,
        "evidence_refs": evidence_refs,
        "tool_calls": tool_calls_payload,
        "audit_ref": audit_ref,
        "role_views_available": [
            "team",
            "technical",
            "business",
            "qa",
            "manager",
            "evidence",
            "onboarding",
            "debug",
            "map",
        ],
    }


def _compose_summary(question: str, calls: tuple[HermesLoopToolCall, ...]) -> str:
    if not calls:
        return f"Hermes 已处理「{question}」，但未调用只读工具。"
    tool_names = "、".join(call.name for call in calls)
    return f"针对「{question}」已通过 Hermes LLM/tool loop 调查：{tool_names}。"


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


async def _run_runner_async(
    runner: HermesLoopRunner,
    *,
    question: str,
    project: ProjectRef,
    user_id: str,
    chat_id: str,
) -> HermesLoopRunResult:
    import asyncio

    return await asyncio.to_thread(
        runner.run,
        question=question,
        project=project,
        user_id=user_id,
        chat_id=chat_id,
    )


def _run_async(coro: Any) -> Any:
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        return _run_in_new_loop(coro)
    return loop.run_until_complete(coro)


def _run_in_new_loop(coro: Any) -> Any:
    import asyncio
    import threading

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            error["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error["error"]
    return result.get("value")
