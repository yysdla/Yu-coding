"""One-shot Project Agent HTTP routes for Hermes/MCP Kernel interface."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from project_lens.api.project_agent_schemas import (
    ProjectAgentAskRequestBody,
    ProjectAgentAskResponse,
    ProjectAgentToolCallRequestBody,
    ProjectAgentToolCallResponse,
    ProjectAgentToolsResponse,
    ProjectAgentRoleViewRequestBody,
    ProjectAgentRoleViewResponse,
)
from project_lens.application.project_agent_ask import (
    ProjectAgentAskRequest,
    ProjectAgentAskService,
)
from project_lens.application.project_agent_tools import (
    ProjectAgentToolCallRequest,
    ProjectAgentToolService,
)
from project_lens.application.project_agent_role_view import (
    ProjectAgentRoleViewService,
    RoleViewReplayRequest,
)

router = APIRouter(tags=["project-agent"])


def get_project_agent_ask_service(request: Request) -> ProjectAgentAskService:
    service = getattr(request.app.state, "project_agent_ask_service", None)
    if service is None:
        raise RuntimeError("project_agent_ask_service is not configured")
    return service


def get_project_agent_role_view_service(request: Request) -> ProjectAgentRoleViewService:
    service = getattr(request.app.state, "project_agent_role_view_service", None)
    if service is None:
        # Same ask RunService is enough for replay (shared repository).
        ask = get_project_agent_ask_service(request)
        return ProjectAgentRoleViewService(run_service=ask.run_service)
    return service


def get_project_agent_tool_service(request: Request) -> ProjectAgentToolService:
    service = getattr(request.app.state, "project_agent_tool_service", None)
    if service is None:
        raise RuntimeError("project_agent_tool_service is not configured")
    return service


@router.get(
    "/project-agent/tools",
    response_model=ProjectAgentToolsResponse,
)
def project_agent_tools(
    service: ProjectAgentToolService = Depends(get_project_agent_tool_service),
) -> ProjectAgentToolsResponse:
    """List the ProjectLens read-only tool envelope for Hermes/runtime agents."""

    return ProjectAgentToolsResponse.model_validate(service.list_tools())


@router.post(
    "/project-agent/tools/call",
    response_model=ProjectAgentToolCallResponse,
)
async def project_agent_tool_call(
    payload: ProjectAgentToolCallRequestBody,
    service: ProjectAgentToolService = Depends(get_project_agent_tool_service),
) -> ProjectAgentToolCallResponse:
    """Call one ProjectLens read-only tool through ProjectSpace/RolePolicy/Gateway."""

    result = await service.call_tool(
        ProjectAgentToolCallRequest(
            tool_name=payload.tool_name,
            project=payload.project,
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            arguments=payload.arguments,
        )
    )
    return ProjectAgentToolCallResponse.model_validate(result)


@router.post(
    "/project-agent/ask",
    response_model=ProjectAgentAskResponse,
)
async def project_agent_ask(
    payload: ProjectAgentAskRequestBody,
    service: ProjectAgentAskService = Depends(get_project_agent_ask_service),
) -> ProjectAgentAskResponse:
    """Create + execute a read_agent run and return a compact answer envelope.

    Does not invent facts. Does not query EvidenceIndex / graph / ops directly.
    """

    result = await service.ask(
        ProjectAgentAskRequest(
            question=payload.question,
            project=payload.project,
            user_id=payload.user_id,
            channel_id=payload.channel_id,
            audience=payload.audience,
            mode=payload.mode,
            format=payload.format,
        )
    )
    return ProjectAgentAskResponse.model_validate(result)


@router.post(
    "/project-agent/runs/{run_id}/role-view",
    response_model=ProjectAgentRoleViewResponse,
)
async def project_agent_role_view(
    run_id: UUID,
    payload: ProjectAgentRoleViewRequestBody,
    service: ProjectAgentRoleViewService = Depends(get_project_agent_role_view_service),
) -> ProjectAgentRoleViewResponse | JSONResponse:
    """Replay RoleView Markdown for an existing completed run.

    Does not execute investigation again. Does not invent facts.
    """

    result = service.role_view(
        RoleViewReplayRequest(run_id=run_id, audience=payload.audience)
    )
    http_status = result.pop("http_status", None) if isinstance(result, dict) else None
    body = ProjectAgentRoleViewResponse.model_validate(result)
    if body.ok:
        return body
    code = {
        400: status.HTTP_400_BAD_REQUEST,
        404: status.HTTP_404_NOT_FOUND,
        409: status.HTTP_409_CONFLICT,
    }.get(int(http_status) if http_status else 0)
    if code is None:
        # Fallback: map known error codes when http_status was omitted.
        if body.error_code == "RUN_NOT_FOUND":
            code = status.HTTP_404_NOT_FOUND
        elif body.error_code == "RUN_NOT_READY":
            code = status.HTTP_409_CONFLICT
        elif body.error_code == "INVALID_AUDIENCE":
            code = status.HTTP_400_BAD_REQUEST
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=body.message or "role-view failed",
            )
    return JSONResponse(status_code=code, content=body.model_dump(mode="json"))
