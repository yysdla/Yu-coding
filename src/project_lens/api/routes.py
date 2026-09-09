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
    ReadinessResponse,
    ProjectKnowledgeGapsRequest,
    ProjectKnowledgeGapsResponse,
    ProjectFactResolutionRequest,
    ProjectFactResolutionResponse,
    ProjectSourceGapsRequest,
    ProjectSourceGapsResponse,
    ProjectSourceRecordsRequest,
    ProjectSourceRecordsResponse,
    ProjectConnectorSyncStatusRequest,
    ProjectConnectorSyncStatusResponse,
    ProjectConnectorSyncRequest,
    ProjectConnectorSyncResponse,
    ConnectorSyncResultResponse,
    ProjectWikiDraftRequest,
    ProjectWikiDraftResponse,
    WikiPageDraftResponse,
    ProjectWikiReviewRequest,
    ProjectWikiReviewResponse,
    ConnectorSyncStatusResponse,
    SourceRecordResponse,
    SourceGapResponse,
    ProjectSnapshotRequest,
    ProjectSnapshotResponse,
    ProjectChangeImpactRequest,
    ProjectChangeImpactResponse,
    ProjectTimelineRequest,
    ProjectTimelineResponse,
)
from project_lens.application.feishu_doc_sync import FeishuDocumentSyncService
from project_lens.application.feishu_doc_sync_status import FeishuDocSyncStatusStore
from project_lens.application.connector_sync_status import ConnectorSyncStateStore, sync_state_is_stale
from project_lens.application.connector_sync import ConnectorSyncService
from project_lens.application.project_connector_factory import ProjectConnectorFactory
from project_lens.application.wiki_compiler import WikiDraftCompiler
from project_lens.application.wiki_compiler import WikiDraftWorkflowService
from project_lens.application.run_service import RunService
from project_lens.context.bootstrap import LocalProjectRegistration
from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext
from project_lens.context.source_store import SourceRecordStore
from project_lens.domain.feishu_doc_sync import FeishuDocSyncStatusValue
from project_lens.domain.models import AgentRun
from project_lens.application.release_gates import evaluate_release_gates
from project_lens.config import settings

router = APIRouter()


def get_run_service(request: Request) -> RunService:
    return request.app.state.run_service


def get_context_engine(request: Request) -> ContextEngine:
    return request.app.state.context_engine


def get_source_record_store(request: Request) -> SourceRecordStore | None:
    return getattr(request.app.state, "source_record_store", None)


def get_connector_sync_state_store(request: Request) -> ConnectorSyncStateStore | None:
    return getattr(request.app.state, "connector_sync_state_store", None)


def get_connector_sync_service(request: Request) -> ConnectorSyncService | None:
    return getattr(request.app.state, "connector_sync_service", None)


def get_project_connector_factory(request: Request) -> ProjectConnectorFactory | None:
    return getattr(request.app.state, "project_connector_factory", None)


def get_wiki_draft_compiler(request: Request) -> WikiDraftCompiler | None:
    return getattr(request.app.state, "wiki_draft_compiler", None)


def get_wiki_draft_workflow(request: Request) -> WikiDraftWorkflowService | None:
    return getattr(request.app.state, "wiki_draft_workflow", None)


def get_feishu_doc_sync(request: Request) -> FeishuDocumentSyncService | None:
    return getattr(request.app.state, "feishu_doc_sync_service", None)


def get_feishu_doc_sync_status_store(request: Request) -> FeishuDocSyncStatusStore | None:
    return getattr(request.app.state, "feishu_doc_sync_status_store", None)


def get_local_registrations(request: Request) -> tuple[LocalProjectRegistration, ...]:
    return getattr(request.app.state, "local_project_registrations", ())


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="project-lens", version=__version__)


@router.get("/ready", response_model=ReadinessResponse, tags=["system"])
def ready(request: Request) -> ReadinessResponse:
    """Readiness is stricter than liveness and reports safe operational checks."""
    checks: dict[str, object] = {}
    checks["context_engine"] = getattr(request.app.state, "context_engine", None) is not None
    checks["run_service"] = getattr(request.app.state, "run_service", None) is not None
    checks["project_registry"] = getattr(request.app.state, "project_registry", None) is not None
    checks["hermes_runtime"] = (
        getattr(request.app.state, "hermes_runtime_service", None) is not None
    )
    checks["hermes_bridge"] = (
        getattr(request.app.state, "feishu_hermes_tool_loop_bridge", None) is not None
    )
    checks["risk_engine"] = getattr(request.app.state, "risk_engine", None) is not None
    checks["database"] = getattr(request.app.state, "database", None) is not None
    checks["service_auth"] = (
        settings.env.strip().lower() not in {"pilot", "production"}
        or bool((settings.service_token or "").strip())
    )
    ready_state = all(bool(value) for value in checks.values())
    return ReadinessResponse(
        status="ready" if ready_state else "not_ready",
        service="project-lens",
        version=__version__,
        ready=ready_state,
        checks=checks,
    )


@router.get("/release-gate", tags=["system"])
def release_gate(request: Request) -> dict[str, object]:
    readiness = ready(request)
    report = evaluate_release_gates(
        mode=settings.env.strip().lower(),
        service_auth_configured=bool((settings.service_token or "").strip()),
        readiness_ok=readiness.ready,
        citation_coverage=getattr(request.app.state, "citation_coverage", None),
        unauthorized_writes=getattr(request.app.state, "unauthorized_writes", None),
        leakage_count=getattr(request.app.state, "leakage_count", None),
        success_rate=getattr(request.app.state, "success_rate", None),
        fallback_rate=getattr(request.app.state, "fallback_rate", None),
        duplicate_notification_rate=getattr(request.app.state, "duplicate_notification_rate", None),
        replay_sample_count=int(getattr(request.app.state, "replay_sample_count", 0) or 0),
        pilot_days=int(getattr(request.app.state, "pilot_days", 0) or 0),
    )
    return {"ready": report.ready, "mode": report.mode, "checks": report.checks, "blockers": list(report.blockers), "note": "Missing measured metrics keep the service in internal technical pilot status."}


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
    "/projects/fact-resolution",
    response_model=ProjectFactResolutionResponse,
    tags=["projects"],
)
def project_fact_resolution(
    payload: ProjectFactResolutionRequest,
    context_engine: ContextEngine = Depends(get_context_engine),
) -> ProjectFactResolutionResponse:
    result = context_engine.resolve_fact(
        payload.project,
        AccessContext(
            tenant_id=payload.project.tenant_id,
            user_id=payload.user_id,
            permissions=frozenset(payload.permissions),
        ),
        payload.fact_type,
    )
    return ProjectFactResolutionResponse(
        fact_type=result.fact_type,
        selected=SourceRecordResponse.from_source(result.selected) if result.selected else None,
        candidates=tuple(SourceRecordResponse.from_source(item) for item in result.candidates),
        conflicts=tuple(SourceRecordResponse.from_source(item) for item in result.conflicts),
        reason=result.reason,
    )


@router.post(
    "/projects/source-gaps",
    response_model=ProjectSourceGapsResponse,
    tags=["projects"],
)
def project_source_gaps(
    payload: ProjectSourceGapsRequest,
    context_engine: ContextEngine = Depends(get_context_engine),
) -> ProjectSourceGapsResponse:
    gaps = context_engine.source_gaps(
        payload.project,
        AccessContext(
            tenant_id=payload.project.tenant_id,
            user_id=payload.user_id,
            permissions=frozenset(payload.permissions),
        ),
    )
    return ProjectSourceGapsResponse(
        project=payload.project,
        gaps=tuple(
            SourceGapResponse(
                status=item.status,
                fact_type=item.fact_type,
                project_id=item.project_id,
                reason=item.reason,
                evidence_ids=item.evidence_ids,
                suggested_source_type=item.suggested_source_type.value if item.suggested_source_type else None,
            )
            for item in gaps
        ),
    )


@router.post(
    "/projects/source-records",
    response_model=ProjectSourceRecordsResponse,
    tags=["projects"],
)
def project_source_records(
    payload: ProjectSourceRecordsRequest,
    context_engine: ContextEngine = Depends(get_context_engine),
    source_store: SourceRecordStore | None = Depends(get_source_record_store),
) -> ProjectSourceRecordsResponse:
    if source_store is None:
        return ProjectSourceRecordsResponse(project=payload.project)
    allowed = frozenset(payload.permissions)
    records = source_store.all(
        tenant_id=payload.project.tenant_id,
        project_id=payload.project.project_id,
        include_revoked=payload.include_revoked,
    )
    visible = [
        item
        for item in records
        if item.access_scope in allowed
        and (payload.source_id is None or item.source_id == payload.source_id)
    ]
    evidence = context_engine.authorized_evidence(
        payload.project,
        AccessContext(
            tenant_id=payload.project.tenant_id,
            user_id=payload.user_id,
            permissions=allowed,
        ),
        limit=50,
    )
    return ProjectSourceRecordsResponse(
        project=payload.project,
        records=tuple(
            SourceRecordResponse.from_source(item).model_copy(
                update={"evidence_refs": _source_evidence_refs(item, evidence)}
            )
            for item in sorted(visible, key=lambda item: item.observed_at, reverse=True)
        ),
    )


@router.post(
    "/projects/connector-sync-status",
    response_model=ProjectConnectorSyncStatusResponse,
    tags=["projects"],
)
def project_connector_sync_status(
    payload: ProjectConnectorSyncStatusRequest,
    state_store: ConnectorSyncStateStore | None = Depends(get_connector_sync_state_store),
    registrations: tuple[LocalProjectRegistration, ...] = Depends(get_local_registrations),
) -> ProjectConnectorSyncStatusResponse:
    registration = _match_registration(payload.project, registrations)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project is not registered")
    if registration.access_scope not in payload.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing project access scope for connector sync status",
        )
    states = (
        state_store.list_for_project(payload.project.tenant_id, payload.project.project_id)
        if state_store is not None
        else ()
    )
    return ProjectConnectorSyncStatusResponse(
        project=payload.project,
        statuses=tuple(
            ConnectorSyncStatusResponse.from_state(
                item,
                stale=sync_state_is_stale(
                    item,
                    max_age_seconds=settings.connector_freshness_max_age_seconds,
                ),
            )
            for item in states
        ),
    )


@router.post(
    "/projects/connectors/sync",
    response_model=ProjectConnectorSyncResponse,
    tags=["projects"],
)
async def sync_project_connectors(
    payload: ProjectConnectorSyncRequest,
    sync_service: ConnectorSyncService | None = Depends(get_connector_sync_service),
    connector_factory: ProjectConnectorFactory | None = Depends(get_project_connector_factory),
    registrations: tuple[LocalProjectRegistration, ...] = Depends(get_local_registrations),
) -> ProjectConnectorSyncResponse:
    registration = _match_registration(payload.project, registrations)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project is not registered")
    if registration.access_scope not in payload.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing project access scope for connector sync",
        )
    if sync_service is None or connector_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="connector sync is not configured",
        )
    try:
        connectors = connector_factory.build(
            tenant_id=payload.project.tenant_id,
            project_id=payload.project.project_id,
            names=payload.connectors,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if payload.connectors and not connectors:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no requested syncable connector is configured for this project",
        )
    results = tuple(
        await sync_service.sync_connector(connector, project=registration.project)
        for connector in connectors
    )
    return ProjectConnectorSyncResponse(
        project=registration.project,
        results=tuple(
            ConnectorSyncResultResponse(
                connector=item.connector,
                ok=item.ok,
                added_count=item.added_count,
                updated_count=item.updated_count,
                revoked_count=item.revoked_count,
                indexed_count=item.state.indexed_count,
                failed_count=item.state.failed_count,
                cursor=item.state.cursor.token if item.state.cursor else None,
                stale=item.stale,
                freshness_warning=item.freshness_warning,
                error=item.error,
            )
            for item in results
        ),
    )


@router.post(
    "/projects/wiki/draft",
    response_model=ProjectWikiDraftResponse,
    tags=["projects"],
)
def project_wiki_draft(
    payload: ProjectWikiDraftRequest,
    compiler: WikiDraftCompiler | None = Depends(get_wiki_draft_compiler),
    registrations: tuple[LocalProjectRegistration, ...] = Depends(get_local_registrations),
) -> ProjectWikiDraftResponse:
    registration = _match_registration(payload.project, registrations)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project is not registered")
    if registration.access_scope not in payload.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing project access scope for Wiki draft")
    if compiler is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Wiki draft compiler is not configured")
    pages = (
        compiler.compile(tenant_id=payload.project.tenant_id, project_id=payload.project.project_id, access_scopes=frozenset(payload.permissions))
        if payload.compile
        else compiler.list_for_project(payload.project.tenant_id, payload.project.project_id)
    )
    return ProjectWikiDraftResponse(project=registration.project, pages=tuple(WikiPageDraftResponse.from_draft(item) for item in pages))


@router.post(
    "/projects/wiki/review",
    response_model=ProjectWikiReviewResponse,
    tags=["projects"],
)
def project_wiki_review(
    payload: ProjectWikiReviewRequest,
    workflow: WikiDraftWorkflowService | None = Depends(get_wiki_draft_workflow),
    registrations: tuple[LocalProjectRegistration, ...] = Depends(get_local_registrations),
) -> ProjectWikiReviewResponse:
    registration = _match_registration(payload.project, registrations)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project is not registered")
    if registration.access_scope not in payload.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="missing project access scope for Wiki review")
    if workflow is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Wiki workflow is not configured")
    try:
        review = workflow.review(
            tenant_id=payload.project.tenant_id,
            project_id=payload.project.project_id,
            page_type=payload.page_type,
            approved=payload.approved,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    page = review.draft
    return ProjectWikiReviewResponse(
        project=registration.project,
        page=WikiPageDraftResponse.from_draft(page),
        published_uri=review.published_uri,
    )


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


def _source_evidence_refs(source: object, evidence: tuple[object, ...]) -> tuple[str, ...]:
    source_id = getattr(source, "source_id", "")
    revision = getattr(source, "revision", "")
    refs: list[str] = []
    for item in evidence:
        item_source = getattr(item, "source", None)
        metadata = getattr(item, "metadata", {})
        item_id = getattr(item, "id", None)
        if item_id is None or not isinstance(metadata, dict):
            continue
        item_source_id = getattr(item_source, "source_id", "")
        matches_source = item_source_id == source_id or metadata.get("doc_token") == source_id
        matches_revision = not revision or str(metadata.get("revision") or metadata.get("version") or "") == revision
        if matches_source and matches_revision:
            refs.append(str(item_id))
    return tuple(refs)


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
    except (RuntimeError, ValueError) as exc:
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
