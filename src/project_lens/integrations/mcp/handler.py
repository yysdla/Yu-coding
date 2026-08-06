"""In-process MCP handlers for ProjectLens tools.

Callable without the optional ``mcp`` package so unit tests stay lightweight.
Formal Agent tool loop uses the five projectlens_* read-only tools via
ProjectAgentToolService (same logic as GET/POST /project-agent/tools[/call]).
projectlens_ask_project remains debug/smoke only.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from project_lens.application.project_agent_ask import (
    ProjectAgentAskRequest,
    ProjectAgentAskService,
)
from project_lens.application.project_agent_tools import (
    ProjectAgentToolCallRequest,
    ProjectAgentToolService,
)
from project_lens.domain.models import ProjectRef

# Debug / smoke umbrella tool — not part of the formal Agent tool loop.
TOOL_NAME = "projectlens_ask_project"

TOOL_DESCRIPTION = (
    "[DEBUG/SMOKE] One-shot ProjectLens ask for smoke tests and slash-command "
    "debugging. Do not use this as the formal Hermes Agent tool loop. "
    "Prefer the projectlens_* read-only tools from list_projectlens_tools / "
    "GET /api/v1/project-agent/tools. Apply / PR / deploy / rollback / restart "
    "remain disabled."
)

FORMAL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "projectlens_search_context",
        "projectlens_read_project_file",
        "projectlens_query_graph",
        "projectlens_authorized_evidence",
        "projectlens_list_knowledge_gaps",
    }
)


def build_ask_service_from_app(app: FastAPI) -> ProjectAgentAskService:
    service = getattr(app.state, "project_agent_ask_service", None)
    if service is None:
        raise RuntimeError("app.state.project_agent_ask_service is not configured")
    return service


def build_tool_service_from_app(app: FastAPI) -> ProjectAgentToolService:
    service = getattr(app.state, "project_agent_tool_service", None)
    if service is None:
        raise RuntimeError("app.state.project_agent_tool_service is not configured")
    return service


def list_projectlens_tools(service: ProjectAgentToolService) -> dict[str, Any]:
    """Return the formal read-only tool catalog (same as GET /project-agent/tools)."""

    return service.list_tools()


async def call_projectlens_tool(
    service: ProjectAgentToolService,
    *,
    tool_name: str,
    project_id: str,
    tenant_id: str = "demo",
    user_id: str = "mcp-user",
    chat_id: str = "mcp-chat",
    arguments: dict[str, Any] | None = None,
    service_name: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """Call one formal ProjectLens tool via ProjectAgentToolService.

    Same path as POST /api/v1/project-agent/tools/call — no direct EvidenceIndex,
    graph store, or filesystem access from the MCP adapter.
    """

    return await service.call_tool(
        ProjectAgentToolCallRequest(
            tool_name=tool_name,
            project=ProjectRef(
                tenant_id=tenant_id,
                project_id=project_id,
                service=service_name,
                environment=environment,
            ),
            user_id=user_id,
            chat_id=chat_id,
            arguments=arguments or {},
        )
    )


async def ask_project(
    service: ProjectAgentAskService,
    *,
    question: str,
    project_id: str,
    tenant_id: str = "demo",
    user_id: str = "mcp-user",
    channel_id: str | None = None,
    audience: str = "team",
    format: str = "concise",
    service_name: str | None = None,
    environment: str | None = None,
) -> dict[str, Any]:
    """Debug/smoke one-shot ask via ProjectAgentAskService (not formal tool loop)."""

    return await service.ask(
        ProjectAgentAskRequest(
            question=question,
            project=ProjectRef(
                tenant_id=tenant_id,
                project_id=project_id,
                service=service_name,
                environment=environment,
            ),
            user_id=user_id,
            channel_id=channel_id,
            audience=audience,
            mode="read_only",
            format=format,
        )
    )
