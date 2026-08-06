"""ProjectLens HTTP routes."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from project_lens import __version__
from project_lens.api.schemas import (
    AgentEventResponse,
    CreateRunRequest,
    CreateRunResponse,
    FeishuDocsSyncRequest,
    FeishuDocsSyncResponse,
    FeishuDocsSyncStatusRequest,
    FeishuDocsSyncStatusResponse,
    HealthResponse,
    ProjectKnowledgeGapsRequest,
    ProjectKnowledgeGapsResponse,
    ProjectSnapshotRequest,
    ProjectSnapshotResponse,
    ProjectChangeImpactRequest,
    ProjectChangeImpactResponse,
    ProjectTimelineRequest,
    ProjectTimelineResponse,
)
from project_lens.application.feishu_doc_sync import FeishuDocumentSyncService
from project_lens.application.feishu_doc_sync_status import FeishuDocSyncStatusStore
from project_lens.application.run_service import RunService
from project_lens.context.bootstrap import LocalProjectRegistration
from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext
from project_lens.domain.feishu_doc_sync import FeishuDocSyncStatusValue
from project_lens.domain.models import AgentRun

router = APIRouter()


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service


def get_context_engine(request: Request) -> ContextEngine:
    return request.app.state.context_engine


def get_feishu_doc_sync(request: Request) -> FeishuDocumentSyncService | None:
    return getattr(request.app.state, "feishu_doc_sync_service", None)


def get_feishu_doc_sync_status_store(request: Request) -> FeishuDocSyncStatusStore | None:
    return getattr(request.app.state, "feishu_doc_sync_status_store", None)


def get_local_registrations(request: Request) -> tuple[LocalProjectRegistration, ...]:
    return getattr(request.app.state, "local_project_registrations", ())


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="project-lens", version=__version__)


@router.post(
    "/projects/snapshot",
    response_model=ProjectSnapshotResponse,
    tags=["projects"],
)
def project_snapshot(
    payload: ProjectSnapshotRequest,
    context_engine: ContextEngine = Depends(get_context_engine),
) -> ProjectSnapshotResponse:
    snapshot = context_engine.snapshot(
        payload.project,
        AccessContext(
            tenant_id=payload.project.tenant_id,
            user_id=payload.user_id,
            permissions=frozenset(payload.permissions),
        ),
    )
    return ProjectSnapshotResponse.model_validate(snapshot.model_dump())


@router.post(
    "/projects/timeline",
    response_model=ProjectTimelineResponse,
    tags=["projects"],
)
def project_timeline(
    payload: ProjectTimelineRequest,
    context_engine: ContextEngine = Depends(get_context_engine),
) -> ProjectTimelineResponse:
    events = context_engine.timeline(
        payload.project,
        AccessContext(
            tenant_id=payload.project.tenant_id,
            user_id=payload.user_id,
            permissions=frozenset(payload.permissions),
        ),
        time_range=payload.time_range,
        limit=payload.limit,
    )
    return ProjectTimelineResponse(events=events)


@router.post(
    "/projects/change-impact",
    response_model=ProjectChangeImpactResponse,
    tags=["projects"],
)
def project_change_impact(
    payload: ProjectChangeImpactRequest,
    context_engine: ContextEngine = Depends(get_context_engine),
) -> ProjectChangeImpactResponse:
    impact = context_engine.change_impact(
        payload.project,
        AccessContext(
            tenant_id=payload.project.tenant_id,
            user_id=payload.user_id,
            permissions=frozenset(payload.permissions),
        ),
        time_range=payload.time_range,
        limit=payload.limit,
    )
    return ProjectChangeImpactResponse.model_validate(impact.model_dump())


@router.post(
    "/projects/knowledge-gaps",
    response_model=ProjectKnowledgeGapsResponse,
    tags=["projects"],
)
def project_knowledge_gaps(
    payload: ProjectKnowledgeGapsRequest,
    context_engine: ContextEngine = Depends(get_context_engine),
) -> ProjectKnowledgeGapsResponse:
    report = context_engine.knowledge_gaps(
        payload.project,
        AccessContext(
            tenant_id=payload.project.tenant_id,
            user_id=payload.user_id,
            permissions=frozenset(payload.permissions),
        ),
    )
    return ProjectKnowledgeGapsResponse.model_validate(report.model_dump())


@router.post(
    "/projects/feishu-docs/sync",
    response_model=FeishuDocsSyncResponse,
    tags=["projects"],
)
def sync_feishu_docs(
    payload: FeishuDocsSyncRequest,
    sync_service: FeishuDocumentSyncService | None = Depends(get_feishu_doc_sync),
    registrations: tuple[LocalProjectRegistration, ...] = Depends(get_local_registrations),
) -> FeishuDocsSyncResponse:
    if sync_service is None or not sync_service.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feishu document API sync is not configured",
        )
    registration = _match_registration(payload.project, registrations)
    if registration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project is not registered for local sync",
        )
    access_scope = registration.access_scope
    if access_scope not in payload.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing project access scope for Feishu document sync",
        )
    tokens = payload.doc_tokens or registration.sources.feishu_doc_tokens
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no feishu_doc_tokens configured for this project",
        )
    try:
        result = sync_service.sync_tokens(
            project=registration.project,
            access_scope=access_scope,
            doc_tokens=tokens,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return FeishuDocsSyncResponse(
        project_id=result.project_id,
        requested=result.requested,
        fetched=result.fetched,
        indexed=result.indexed,
        skipped_unchanged=result.skipped_unchanged,
        failed=result.failed,
        statuses=result.statuses,
    )


@router.post(
    "/projects/feishu-docs/sync-status",
    response_model=FeishuDocsSyncStatusResponse,
    tags=["projects"],
)
def feishu_docs_sync_status(
    payload: FeishuDocsSyncStatusRequest,
    status_store: FeishuDocSyncStatusStore | None = Depends(get_feishu_doc_sync_status_store),
    registrations: tuple[LocalProjectRegistration, ...] = Depends(get_local_registrations),
) -> FeishuDocsSyncStatusResponse:
    registration = _match_registration(payload.project, registrations)
    if registration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="project is not registered for local sync",
        )
    if registration.access_scope not in payload.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing project access scope for Feishu document sync status",
        )
    if status_store is None:
        return FeishuDocsSyncStatusResponse(project=registration.project, statuses=())
    statuses = status_store.list_for_project(registration.project)
    return FeishuDocsSyncStatusResponse(
        project=registration.project,
        statuses=statuses,
        success_count=sum(
            1 for item in statuses if item.status == FeishuDocSyncStatusValue.SUCCESS
        ),
        skipped_count=sum(
            1 for item in statuses if item.status == FeishuDocSyncStatusValue.SKIPPED
        ),
        failed_count=sum(
            1 for item in statuses if item.status == FeishuDocSyncStatusValue.FAILED
        ),
    )


def _match_registration(
    project: object,
    registrations: tuple[LocalProjectRegistration, ...],
) -> LocalProjectRegistration | None:
    from project_lens.domain.models import ProjectRef

    if not isinstance(project, ProjectRef):
        return None
    for item in registrations:
        if (
            item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
        ):
            return item
    return None


@router.post(
    "/runs",
    response_model=CreateRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
)
def create_run(
    payload: CreateRunRequest,
    service: RunService = Depends(get_run_service),
) -> CreateRunResponse:
    run = service.create(
        project=payload.project,
        user_id=payload.user_id,
        channel_id=payload.channel_id,
        question=payload.question,
    )
    return CreateRunResponse.from_run(run)


@router.get("/runs/{run_id}", response_model=AgentRun, tags=["runs"])
def get_run(
    run_id: UUID,
    service: RunService = Depends(get_run_service),
) -> AgentRun:
    run = service.get(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@router.post("/runs/{run_id}/execute", response_model=AgentRun, tags=["runs"])
async def execute_run(
    run_id: UUID,
    service: RunService = Depends(get_run_service),
) -> AgentRun:
    try:
        run = await service.execute(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@router.get(
    "/runs/{run_id}/events",
    response_model=list[AgentEventResponse],
    tags=["runs"],
)
def get_run_events(
    run_id: UUID,
    service: RunService = Depends(get_run_service),
) -> list[AgentEventResponse]:
    if not service.get(run_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return [AgentEventResponse.from_event(event) for event in service.events(run_id)]
