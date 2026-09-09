"""One-shot Project Agent HTTP routes for Hermes/MCP Kernel interface."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from project_lens.api.trusted_actor import (
    actor_context_from_headers,
    get_trusted_actor_context,
    resolve_project_agent_actor,
)
from project_lens.api.project_agent_schemas import (
    ProjectAgentAskRequestBody,
    ProjectAgentAskResponse,
    ProjectAgentToolCallRequestBody,
    ProjectAgentToolCallResponse,
    ProjectAgentToolsResponse,
    ProjectAgentRoleViewRequestBody,
    ProjectAgentRoleViewResponse,
    ProjectAgentRunDetailRequestBody,
    ProjectAgentRunDetailResponse,
)
from project_lens.application.project_agent_ask import (
    ProjectAgentAskRequest,
    ProjectAgentAskService,
)
from project_lens.application.project_agent_tools import (
    ProjectAgentToolCallRequest,
    ProjectAgentToolService,
)
from project_lens.application.project_space_inspect import (
    ProjectSpaceInspectService,
    ProjectSpaceScopeInspectRequest,
)
from project_lens.application.project_agent_role_view import (
    ProjectAgentRoleViewService,
    RoleViewReplayRequest,
)
from project_lens.application.project_agent_run_detail import (
    ProjectAgentRunDetailRequest,
    ProjectAgentRunDetailService,
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


def get_project_agent_run_detail_service(request: Request) -> ProjectAgentRunDetailService:
    service = getattr(request.app.state, "project_agent_run_detail_service", None)
    if service is None:
        ask = get_project_agent_ask_service(request)
        return ProjectAgentRunDetailService(
            run_service=ask.run_service,
            project_runtime_context_resolver=request.app.state.project_runtime_context_resolver,
        )
    return service


def get_project_agent_tool_service(request: Request) -> ProjectAgentToolService:
    service = getattr(request.app.state, "project_agent_tool_service", None)
    if service is None:
        raise RuntimeError("project_agent_tool_service is not configured")
    return service


def get_project_space_inspect_service(request: Request) -> ProjectSpaceInspectService:
    service = getattr(request.app.state, "project_space_inspect_service", None)
    if service is None:
        raise RuntimeError("project_space_inspect_service is not configured")
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


@router.get("/project-agent/project-spaces")
def project_agent_project_spaces(
    service: ProjectSpaceInspectService = Depends(get_project_space_inspect_service),
) -> dict[str, object]:
    """List ProjectSpace manifests without exposing indexed file/document contents."""

    return service.list_project_spaces()


@router.get("/project-agent/project-spaces/{tenant_id}/{project_id}")
def project_agent_project_space_detail(
    tenant_id: str,
    project_id: str,
    service: ProjectSpaceInspectService = Depends(get_project_space_inspect_service),
) -> dict[str, object]:
    """Inspect one ProjectSpace manifest and validation report."""

    try:
        return service.project_space_detail(tenant_id=tenant_id, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown project space: {tenant_id}/{project_id}",
        ) from exc


@router.get("/project-agent/project-spaces/{tenant_id}/{project_id}/scope")
def project_agent_project_space_scope(
    request: Request,
    tenant_id: str,
    project_id: str,
    user_id: str,
    chat_id: str,
    service: ProjectSpaceInspectService = Depends(get_project_space_inspect_service),
) -> dict[str, object]:
    """Inspect role/chat effective scope before answer generation."""

    actor = resolve_project_agent_actor(
        request,
        body_user_id=user_id,
        body_channel_id=chat_id,
        project_tenant_id=tenant_id,
    )
    try:
        return service.inspect_scope(
            ProjectSpaceScopeInspectRequest(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=actor.actor_id,
                chat_id=actor.chat_id,
                chat_type=actor.chat_type,
                identity_source=actor.source,
            )
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown project space: {tenant_id}/{project_id}",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


@router.post(
    "/project-agent/tools/call",
    response_model=ProjectAgentToolCallResponse,
)
async def project_agent_tool_call(
    request: Request,
    payload: ProjectAgentToolCallRequestBody,
    service: ProjectAgentToolService = Depends(get_project_agent_tool_service),
) -> ProjectAgentToolCallResponse:
    """Call one ProjectLens read-only tool through ProjectSpace/RolePolicy/Gateway."""

    actor = resolve_project_agent_actor(
        request,
        body_user_id=payload.user_id,
        body_channel_id=payload.chat_id,
        project_tenant_id=payload.project.tenant_id,
    )
    result = await service.call_tool(
        ProjectAgentToolCallRequest(
            tool_name=payload.tool_name,
            project=payload.project,
            user_id=actor.actor_id,
            chat_id=actor.chat_id,
            chat_type=actor.chat_type,
            identity_source=actor.source,
            arguments=payload.arguments,
        )
    )
    return ProjectAgentToolCallResponse.model_validate(result)


@router.post(
    "/project-agent/ask",
    response_model=ProjectAgentAskResponse,
)
async def project_agent_ask(
    request: Request,
    payload: ProjectAgentAskRequestBody,
    service: ProjectAgentAskService = Depends(get_project_agent_ask_service),
) -> ProjectAgentAskResponse:
    """Create + execute a Hermes run and return a compact answer envelope.

    Does not invent facts. Does not query EvidenceIndex / graph / ops directly.
    """

    actor = resolve_project_agent_actor(
        request,
        body_user_id=payload.user_id,
        body_channel_id=payload.channel_id,
        project_tenant_id=payload.project.tenant_id,
    )
    result = await service.ask(
        ProjectAgentAskRequest(
            question=payload.question,
            project=payload.project,
            user_id=actor.actor_id,
            channel_id=actor.chat_id,
            chat_type=actor.chat_type,
            identity_source=actor.source,
            audience=payload.audience,
            mode=payload.mode,
            format=payload.format,
            actor=actor,
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


@router.post(
    "/project-agent/runs/{run_id}/detail",
    response_model=ProjectAgentRunDetailResponse,
)
async def project_agent_run_detail(
    request: Request,
    run_id: UUID,
    payload: ProjectAgentRunDetailRequestBody,
    service: ProjectAgentRunDetailService = Depends(get_project_agent_run_detail_service),
) -> ProjectAgentRunDetailResponse | JSONResponse:
    """Replay a safe run detail view for the existing completed run."""

    try:
        actor = get_trusted_actor_context(request)
    except HTTPException:
        actor = actor_context_from_headers(request)
        if actor is None:
            raise
    run = service.run_detail(
        ProjectAgentRunDetailRequest(
            run_id=run_id,
            actor=actor,
            audience=payload.audience,
        )
    )
    http_status = run.pop("http_status", None) if isinstance(run, dict) else None
    body = ProjectAgentRunDetailResponse.model_validate(run)
    if body.ok:
        return body
    code = {
        400: status.HTTP_400_BAD_REQUEST,
        403: status.HTTP_403_FORBIDDEN,
        404: status.HTTP_404_NOT_FOUND,
        409: status.HTTP_409_CONFLICT,
    }.get(int(http_status) if http_status else 0)
    if code is None:
        if body.error_code == "RUN_NOT_FOUND":
            code = status.HTTP_404_NOT_FOUND
        elif body.error_code in {"ACCESS_DENIED", "ACCESS_SCOPE_MISMATCH"}:
            code = status.HTTP_403_FORBIDDEN
        elif body.error_code in {"ACCESS_SCOPE_MISSING", "ACCESS_SCOPE_INVALID"}:
            code = status.HTTP_409_CONFLICT
        elif body.error_code == "INVALID_AUDIENCE":
            code = status.HTTP_400_BAD_REQUEST
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=body.message or "run detail failed",
            )
    return JSONResponse(status_code=code, content=body.model_dump(mode="json"))


@router.get("/project-agent/hermes-loops/{run_id}/events")
def project_agent_hermes_loop_events(
    run_id: UUID,
    request: Request,
) -> dict[str, object]:
    """List durable audit events for the real AgentRun behind a Hermes loop."""

    sink = getattr(request.app.state, "event_sink", None)
    bus = getattr(request.app.state, "lifecycle_bus", None)
    events: list[dict[str, object]] = []
    if sink is not None and hasattr(sink, "for_run"):
        for event in sink.for_run(run_id):
            events.append(
                {
                    "run_id": str(event.run_id),
                    "trace_id": str(event.trace_id),
                    "type": event.type.value if hasattr(event.type, "value") else str(event.type),
                    "occurred_at": event.occurred_at.isoformat(),
                    "payload": dict(event.payload or {}),
                }
            )
    elif bus is not None:
        for event in bus.for_run(run_id):
            events.append(
                {
                    "run_id": str(event.run_id) if event.run_id else None,
                    "trace_id": str(event.trace_id) if event.trace_id else None,
                    "type": event.type.value if hasattr(event.type, "value") else str(event.type),
                    "occurred_at": event.occurred_at.isoformat(),
                    "payload": dict(event.payload or {}),
                }
            )
    return {
        "ok": True,
        "run_id": str(run_id),
        "allow_apply": False,
        "mode": "feishu_hermes_llm_tool_loop",
        "event_count": len(events),
        "events": events,
    }
