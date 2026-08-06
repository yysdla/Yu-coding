"""Hermes LLM/tool-loop runner for FeishuHermesToolLoopBridge.

Production path uses Hermes ``AIAgent`` with ``enabled_toolsets=["projectlens"]``.
Tests inject a Fake ``HermesLoopRunner``. There is no rule-planner fallback.

Does not modify Hermes core. Does not touch EvidenceIndex / GraphStore /
filesystem — tools go through ProjectLens Tool Envelope only.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import sys
import threading
from pathlib import Path
from typing import Any, Protocol

from project_lens.domain.models import ProjectRef
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.hermes_plugin.tools import (
    FORMAL_TOOL_NAMES,
    TOOLSET_NAME,
    build_openai_tool_schema,
    resolve_tool_catalog,
)


@dataclass(frozen=True)
class ToolLoopRequestContext:
    tenant_id: str
    project_id: str
    user_id: str
    chat_id: str


_REQUEST_CTX: ContextVar[ToolLoopRequestContext | None] = ContextVar(
    "project_lens_feishu_hermes_loop_request",
    default=None,
)
_REGISTER_LOCK = threading.Lock()


@dataclass
class HermesLoopToolCall:
    name: str
    arguments: dict[str, Any]
    envelope: dict[str, Any]


@dataclass
class HermesLoopRunResult:
    final_response: str
    tool_calls: tuple[HermesLoopToolCall, ...] = ()
    messages: tuple[Any, ...] = ()
    ok: bool = True
    error: str | None = None


class HermesLoopRunner(Protocol):
    def run(
        self,
        *,
        question: str,
        project: ProjectRef,
        user_id: str,
        chat_id: str,
    ) -> HermesLoopRunResult: ...


@dataclass
class HermesAIAgentLoopRunner:
    """Real Hermes AIAgent loop locked to the projectlens toolset."""

    client: Any
    config: ProjectLensPluginConfig
    hermes_repo: str = ""
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    max_iterations: int = 8
    _registered: bool = field(default=False, init=False, repr=False)

    def run(
        self,
        *,
        question: str,
        project: ProjectRef,
        user_id: str,
        chat_id: str,
    ) -> HermesLoopRunResult:
        q = question.strip()
        if not q:
            return HermesLoopRunResult(
                final_response="",
                ok=False,
                error="empty question",
            )

        try:
            self._ensure_hermes_importable()
            self._ensure_tools_registered()
            from run_agent import AIAgent
        except Exception as exc:  # noqa: BLE001
            return HermesLoopRunResult(
                final_response="",
                ok=False,
                error=f"Hermes unavailable: {exc}",
            )

        token = _REQUEST_CTX.set(
            ToolLoopRequestContext(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
                user_id=user_id,
                chat_id=chat_id,
            )
        )
        try:
            agent = AIAgent(
                provider=self.provider or None,
                model=self.model,
                quiet_mode=True,
                skip_memory=True,
                skip_context_files=True,
                enabled_toolsets=[TOOLSET_NAME],
                max_iterations=self.max_iterations,
                ephemeral_system_prompt=(
                    "You answer questions about a software project. "
                    "Use only the projectlens_* read-only tools. "
                    "Cite evidence from tool results. Never invent facts. "
                    "Never apply, PR, deploy, rollback, or restart."
                ),
                user_id=user_id,
                chat_id=chat_id,
                platform="feishu",
            )
            raw = agent.run_conversation(q)
        except Exception as exc:  # noqa: BLE001
            return HermesLoopRunResult(
                final_response="",
                ok=False,
                error=f"Hermes agent loop failed: {exc}",
            )
        finally:
            _REQUEST_CTX.reset(token)

        if not isinstance(raw, dict):
            return HermesLoopRunResult(
                final_response=str(raw or ""),
                ok=False,
                error="Hermes returned a non-dict conversation result",
            )

        messages = tuple(raw.get("messages") or ())
        tool_calls = extract_tool_calls_from_messages(messages)
        final_response = str(raw.get("final_response") or "").strip()
        failed = bool(raw.get("failed") or raw.get("error"))
        if failed and not final_response:
            return HermesLoopRunResult(
                final_response="",
                tool_calls=tool_calls,
                messages=messages,
                ok=False,
                error=str(raw.get("error") or "Hermes conversation failed"),
            )
        return HermesLoopRunResult(
            final_response=final_response,
            tool_calls=tool_calls,
            messages=messages,
            ok=True,
            error=None,
        )

    def _ensure_hermes_importable(self) -> None:
        repo = (self.hermes_repo or "").strip()
        if not repo:
            return
        path = str(Path(repo).resolve())
        if path not in sys.path:
            sys.path.insert(0, path)

    def _ensure_tools_registered(self) -> None:
        if self._registered:
            return
        with _REGISTER_LOCK:
            if self._registered:
                return
            from tools.registry import registry

            catalog = resolve_tool_catalog(self.client)
            formal = [
                item
                for item in catalog
                if isinstance(item, dict) and item.get("name") in FORMAL_TOOL_NAMES
            ]
            if {item["name"] for item in formal} != set(FORMAL_TOOL_NAMES):
                raise RuntimeError(
                    "formal projectlens_* catalog incomplete for Hermes loop registration"
                )

            ctx = _HermesRegistryToolContext(registry=registry)
            for spec in formal:
                name = str(spec["name"])
                description = str(spec.get("description") or name)
                schema = build_openai_tool_schema(spec)
                handler = _make_request_scoped_handler(
                    name,
                    client=self.client,
                    config=self.config,
                )
                ctx.register_tool(
                    name=name,
                    toolset=TOOLSET_NAME,
                    schema=schema,
                    handler=handler,
                    description=description,
                    override=True,
                )
            registered = set(registry.get_tool_names_for_toolset(TOOLSET_NAME))
            if not set(FORMAL_TOOL_NAMES).issubset(registered):
                missing = sorted(set(FORMAL_TOOL_NAMES) - registered)
                raise RuntimeError(f"failed to register projectlens tools: missing={missing}")
            self._registered = True


class _HermesRegistryToolContext:
    """Minimal PluginContext-like wrapper over Hermes tools.registry."""

    def __init__(self, *, registry: Any) -> None:
        self._registry = registry

    def register_tool(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Any,
        check_fn: Any = None,
        requires_env: list[Any] | None = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        override: bool = False,
    ) -> None:
        self._registry.register(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            requires_env=requires_env,
            is_async=is_async,
            description=description,
            emoji=emoji,
            override=override,
        )


def _make_request_scoped_handler(
    tool_name: str,
    *,
    client: Any,
    config: ProjectLensPluginConfig,
) -> Any:
    from project_lens.integrations.hermes_plugin.tools import _make_tool_handler

    base = _make_tool_handler(tool_name, client=client, config=config)

    def _handler(args: dict[str, Any] | str | None = None, **kwargs: Any) -> str:
        ctx = _REQUEST_CTX.get()
        if ctx is not None:
            kwargs.setdefault("tenant_id", ctx.tenant_id)
            kwargs.setdefault("project_id", ctx.project_id)
            kwargs.setdefault("user_id", ctx.user_id)
            kwargs.setdefault("chat_id", ctx.chat_id)
        return base(args, **kwargs)

    _handler.__name__ = tool_name
    _handler.__qualname__ = tool_name
    return _handler


def extract_tool_calls_from_messages(
    messages: tuple[Any, ...] | list[Any],
) -> tuple[HermesLoopToolCall, ...]:
    """Parse Hermes/OpenAI-style messages into ProjectLens tool call records."""

    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    records: list[HermesLoopToolCall] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = str(function.get("name") or call.get("name") or "")
                arguments = _parse_arguments(function.get("arguments") or call.get("arguments"))
                if call_id and name:
                    pending[call_id] = (name, arguments)
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            name = str(message.get("name") or "")
            arguments: dict[str, Any] = {}
            if call_id and call_id in pending:
                name, arguments = pending.pop(call_id)
            envelope = _decode_envelope(message.get("content"))
            if not name:
                name = str(envelope.get("tool_name") or "")
            if name:
                records.append(
                    HermesLoopToolCall(name=name, arguments=arguments, envelope=envelope)
                )
    return tuple(records)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        if isinstance(decoded, dict):
            return decoded
    return {}


def _decode_envelope(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": raw}
        if isinstance(decoded, dict):
            return decoded
    return {"ok": False, "error": f"unexpected tool result type: {type(raw).__name__}"}
