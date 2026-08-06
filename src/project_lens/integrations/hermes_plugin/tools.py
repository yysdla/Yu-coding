"""Hermes plugin helpers for ProjectLens formal read-only tools.

Registers tools only when the Hermes plugin ctx exposes ``register_tool``.
Uses Hermes kwargs API (name/toolset/schema/handler) when available; falls
back to positional FakeContext style for unit tests. Does not modify Hermes
core. Does not touch EvidenceIndex / graph / filesystem.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Protocol

from project_lens.integrations.hermes_plugin.client import ProjectLensApiClient
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig

FORMAL_TOOL_NAMES: tuple[str, ...] = (
    "projectlens_search_context",
    "projectlens_read_project_file",
    "projectlens_query_graph",
    "projectlens_authorized_evidence",
    "projectlens_list_knowledge_gaps",
)

TOOLSET_NAME = "projectlens"

_CONTEXT_PARAM_PROPERTIES: dict[str, dict[str, Any]] = {
    "tenant_id": {"type": "string", "description": "ProjectLens tenant id (default from plugin config)"},
    "project_id": {
        "type": "string",
        "description": "ProjectSpace id (default from plugin config / chat binding)",
    },
    "chat_id": {"type": "string", "description": "Chat/channel id for ChatVisibilityPolicy"},
    "user_id": {"type": "string", "description": "Actor user id for RolePolicy"},
}

_DEFAULT_PARAMETERS: dict[str, dict[str, Any]] = {
    "projectlens_search_context": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search text"},
            "limit": {"type": "integer", "description": "1-20 results; default 8"},
        },
        "required": ["query"],
    },
    "projectlens_read_project_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project-relative allowlisted path"},
        },
        "required": ["path"],
    },
    "projectlens_query_graph": {
        "type": "object",
        "properties": {
            "relation": {"type": "string"},
            "start_kind": {"type": "string"},
            "start_label": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": [],
    },
    "projectlens_authorized_evidence": {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
        "required": [],
    },
    "projectlens_list_knowledge_gaps": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

# Fallback catalog when GET /project-agent/tools is unavailable at register time.
# Names must stay identical to ProjectAgentToolService.list_tools().
_FALLBACK_TOOL_SPECS: tuple[dict[str, Any], ...] = tuple(
    {
        "name": name,
        "description": (
            f"ProjectLens read-only tool `{name}`. "
            "Call via ProjectLens POST /project-agent/tools/call. "
            "Apply / PR / deploy / rollback / restart remain disabled."
        ),
        "category": "read",
        "allow_apply": False,
        "parameters": _DEFAULT_PARAMETERS[name],
    }
    for name in FORMAL_TOOL_NAMES
)


class SupportsRegisterTool(Protocol):
    def register_tool(self, *args: Any, **kwargs: Any) -> Any: ...


def ctx_supports_register_tool(ctx: Any) -> bool:
    return callable(getattr(ctx, "register_tool", None))


def register_tool_uses_hermes_kwargs(register_tool: Any) -> bool:
    """True when register_tool looks like Hermes PluginContext.register_tool."""

    try:
        params = inspect.signature(register_tool).parameters
    except (TypeError, ValueError):
        return False
    return "toolset" in params and "schema" in params and "handler" in params


def resolve_tool_catalog(client: ProjectLensApiClient) -> list[dict[str, Any]]:
    """Discover formal tools via GET /project-agent/tools, with safe fallback."""

    envelope = client.list_tools()
    tools = envelope.get("tools") if isinstance(envelope, dict) else None
    if envelope.get("ok") is True and isinstance(tools, list) and tools:
        formal = [
            item
            for item in tools
            if isinstance(item, dict) and item.get("name") in FORMAL_TOOL_NAMES
        ]
        if {item["name"] for item in formal} == set(FORMAL_TOOL_NAMES):
            return formal
    return [dict(item) for item in _FALLBACK_TOOL_SPECS]


def build_openai_tool_schema(spec: dict[str, Any]) -> dict[str, Any]:
    """Build Hermes/OpenAI-style tool schema from ProjectLens catalog entry."""

    name = str(spec["name"])
    description = str(spec.get("description") or name)
    raw_params = spec.get("parameters") if isinstance(spec.get("parameters"), dict) else {}
    if not raw_params.get("properties"):
        raw_params = _DEFAULT_PARAMETERS.get(name, {"type": "object", "properties": {}, "required": []})
    properties = dict(raw_params.get("properties") or {})
    properties.update(_CONTEXT_PARAM_PROPERTIES)
    required = list(raw_params.get("required") or [])
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def build_tool_call_payload(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    config: ProjectLensPluginConfig,
    chat_id: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build POST /project-agent/tools/call body (no Kernel imports)."""

    if project_id or tenant_id:
        project = {
            "tenant_id": tenant_id or config.default_tenant_id,
            "project_id": project_id or config.default_project_id,
        }
    elif chat_id:
        project = config.project_for_chat(chat_id)
    else:
        project = {
            "tenant_id": config.default_tenant_id,
            "project_id": config.default_project_id,
        }
    return {
        "tool_name": tool_name,
        "project": project,
        "user_id": user_id or config.default_user_id,
        "chat_id": chat_id or "hermes-chat",
        "arguments": arguments,
    }


def register_projectlens_tools(
    ctx: Any,
    *,
    client: ProjectLensApiClient,
    config: ProjectLensPluginConfig,
) -> list[str]:
    """Register formal projectlens_* tools when Hermes ctx supports it.

    Returns the list of registered tool names (empty when register_tool is absent).
    Never patches Hermes core.
    """

    if not ctx_supports_register_tool(ctx):
        return []

    use_hermes = register_tool_uses_hermes_kwargs(ctx.register_tool)
    registered: list[str] = []
    for spec in resolve_tool_catalog(client):
        name = str(spec["name"])
        description = str(spec.get("description") or name)
        schema = build_openai_tool_schema(spec)
        handler = _make_tool_handler(name, client=client, config=config)

        if use_hermes:
            ctx.register_tool(
                name=name,
                toolset=TOOLSET_NAME,
                schema=schema,
                handler=handler,
                description=description,
            )
        else:
            # FakeContext / unit-test style: positional name + handler.
            ctx.register_tool(name, handler, description=description)
        registered.append(name)
    return registered


def _make_tool_handler(
    tool_name: str,
    *,
    client: ProjectLensApiClient,
    config: ProjectLensPluginConfig,
) -> Any:
    def _handler(args: dict[str, Any] | str | None = None, **kwargs: Any) -> str:
        # Hermes registry calls handler(args: dict, **kwargs).
        # Unit tests may pass arguments=... or a dict as first positional.
        raw = args if args is not None else kwargs.get("arguments")
        parsed = _normalize_arguments(raw, kwargs)
        chat_id = (
            parsed.pop("chat_id", None)
            or kwargs.get("chat_id")
            or kwargs.get("channel_id")
        )
        user_id = parsed.pop("user_id", None) or kwargs.get("user_id")
        tenant_id = parsed.pop("tenant_id", None)
        project_id = parsed.pop("project_id", None)
        payload = build_tool_call_payload(
            tool_name=tool_name,
            arguments=parsed,
            config=config,
            chat_id=chat_id if isinstance(chat_id, str) else None,
            user_id=user_id if isinstance(user_id, str) else None,
            tenant_id=tenant_id if isinstance(tenant_id, str) else None,
            project_id=project_id if isinstance(project_id, str) else None,
        )
        envelope = client.call_tool(payload)
        return json.dumps(envelope, ensure_ascii=False)

    _handler.__name__ = tool_name
    _handler.__qualname__ = tool_name
    return _handler


def _normalize_arguments(
    arguments: dict[str, Any] | str | None,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return dict(arguments)
    if isinstance(arguments, str) and arguments.strip():
        try:
            decoded = json.loads(arguments)
            if isinstance(decoded, dict):
                return decoded
        except json.JSONDecodeError:
            return {"query": arguments}
    cleaned = {
        key: value
        for key, value in kwargs.items()
        if key
        not in {"chat_id", "channel_id", "user_id", "tenant_id", "project_id", "arguments"}
        and value is not None
    }
    return cleaned
