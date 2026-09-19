"""HTTP routes for project memory proposals and approvals."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from project_lens.application.approval_harness import ApprovalError
from project_lens.application.memory_service import infer_memory_type
from project_lens.application.memory_retrieval_service import MemoryRetrievalService
from project_lens.application.memory_provenance import MemoryProvenanceError, resolve_source_refs
from project_lens.application.memory_review_service import MemoryReviewService
from project_lens.context.memory_store import MemoryStore
from project_lens.context.source_store import SourceRecordStore
from project_lens.context.memory_retrieval import build_memory_summary
from project_lens.domain.memory import MemoryProposal, MemorySourceRef, MemoryType, ProjectMemory, ReviewReason
from project_lens.domain.memory_review import MemoryReviewStatus
from project_lens.domain.models import ClaimType, ProjectRef
from project_lens.api.trusted_actor import get_trusted_actor_context
from project_lens.project_space.policies import ProjectRuntimeContextResolver
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.runtime.memory_approval_gateway import MemoryApprovalGateway

router = APIRouter()


class CreateMemoryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef | None = None
    proposed_by: str | None = Field(default=None, min_length=1, max_length=100)
    claim_text: str = Field(min_length=1, max_length=2_000)
    claim_type: ClaimType = ClaimType.FACT
    memory_type: MemoryType | None = None
    evidence_ids: tuple[UUID, ...] = ()
    reason: str = Field(default="", max_length=2_000)
    allowed_approvers: tuple[str, ...] = ()
    expires_at: datetime | None = None
    replaces_memory_id: UUID | None = None
    source_refs: tuple[MemorySourceRef, ...] = ()
    subject: str | None = None
    claim_slot: str | None = None
    valid_from: datetime | None = None
    review_due_at: datetime | None = None


class CreateTrustedMemoryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(min_length=1, max_length=2_000)
    claim_type: ClaimType = ClaimType.FACT
    memory_type: MemoryType | None = None
    evidence_ids: tuple[UUID, ...] = ()
    source_refs: tuple[MemorySourceRef, ...] = ()
    subject: str | None = None
    claim_slot: str | None = None
    valid_from: datetime | None = None
    review_due_at: datetime | None = None
    replaces_memory_id: UUID | None = None
    reason: str = Field(default="", max_length=2_000)


class DecideMemoryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    decided_by: str | None = Field(default=None, min_length=1, max_length=100)


class RevokeMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="manual revocation", min_length=1, max_length=500)


class ResolveMemoryReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(min_length=1, max_length=2_000)


class MemoryDecisionResponse(BaseModel):
    proposal: MemoryProposal
    memory: ProjectMemory | None = None


def get_memory_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


def get_source_record_store(request: Request) -> SourceRecordStore | None:
    return getattr(request.app.state, "source_record_store", None)


def get_memory_review_service(request: Request) -> MemoryReviewService:
    service = getattr(request.app.state, "memory_review_service", None)
    if isinstance(service, MemoryReviewService):
        return service
    raise HTTPException(status_code=503, detail="memory review service unavailable")


def get_memory_retrieval_service(request: Request) -> MemoryRetrievalService:
    service = getattr(request.app.state, "memory_retrieval_service", None)
    if isinstance(service, MemoryRetrievalService):
        return service
    return MemoryRetrievalService(request.app.state.memory_store)


def _resolve_memory_scope(request: Request, tenant_id: str, project_id: str):
    actor = get_trusted_actor_context(request)
    if actor.tenant_key != tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="memory project scope denied")
    registry = getattr(request.app.state, "project_registry", None)
    if registry is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="project registry unavailable")
    try:
        resolved = ProjectRuntimeContextResolver(project_registry=registry).resolve(
            tenant_id=tenant_id,
            project_id=project_id,
            chat_id=actor.chat_id,
            user_id=actor.actor_id,
            chat_type=actor.chat_type,
            identity_source=actor.source,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory project not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="memory project scope denied") from exc
    return actor, resolved


def get_memory_approval_gateway(request: Request) -> MemoryApprovalGateway:
    gateway = getattr(request.app.state, "memory_approval_gateway", None)
    if isinstance(gateway, MemoryApprovalGateway):
        return gateway
    return MemoryApprovalGateway(request.app.state.memory_store)


def get_lifecycle_bus(request: Request) -> LifecycleBus:
    bus = getattr(request.app.state, "lifecycle_bus", None)
    return bus if isinstance(bus, LifecycleBus) else LifecycleBus()


def _bound_actor(request: Request):
    actor = getattr(request.state, "actor_context", None)
    return actor if actor is not None else None


def _trusted_memory_scope(request: Request, project: ProjectRef):
    actor = _bound_actor(request)
    if actor is None:
        return None, None
    if actor.tenant_key != project.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="memory project scope denied")
    registry = getattr(request.app.state, "project_registry", None)
    if registry is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="project registry unavailable")
    try:
        resolved = ProjectRuntimeContextResolver(project_registry=registry).resolve(
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            chat_id=actor.chat_id,
            user_id=actor.actor_id,
            chat_type=actor.chat_type,
            identity_source=actor.source,
        )
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="memory project scope denied") from exc
    return actor, resolved


@router.post(
    "/projects/memory-proposals",
    response_model=MemoryProposal,
    status_code=status.HTTP_201_CREATED,
    tags=["memory"],
)
def create_memory_proposal(
    request: Request,
    payload: CreateMemoryProposalRequest,
    gateway: MemoryApprovalGateway = Depends(get_memory_approval_gateway),
    lifecycle: LifecycleBus = Depends(get_lifecycle_bus),
) -> MemoryProposal:
    actor = _bound_actor(request)
    if actor is not None and (payload.project is not None or payload.proposed_by is not None):
        raise HTTPException(status_code=422, detail="UNTRUSTED_SCOPE_FIELD")
    project = payload.project
    proposed_by = payload.proposed_by
    resolved = None
    if actor is not None:
        if project is None:
            raise HTTPException(status_code=422, detail="trusted project path is required")
        actor, resolved = _trusted_memory_scope(request, project)
        proposed_by = actor.actor_id
    if project is None or proposed_by is None:
        raise HTTPException(status_code=422, detail="project and proposed_by are required for legacy requests")
    memory_type = payload.memory_type or infer_memory_type(payload.claim_text)
    fields: dict[str, object] = {
        "project": project,
        "proposed_by": proposed_by,
        "claim_text": payload.claim_text,
        "claim_type": payload.claim_type,
        "memory_type": memory_type,
        "evidence_ids": payload.evidence_ids,
        "reason": payload.reason,
        "allowed_approvers": payload.allowed_approvers,
        "source_refs": payload.source_refs,
        "subject": payload.subject,
        "claim_slot": payload.claim_slot,
        "valid_from": payload.valid_from,
        "review_due_at": payload.review_due_at,
    }
    if payload.expires_at is not None:
        fields["expires_at"] = payload.expires_at
    if payload.replaces_memory_id is not None:
        fields["replaces_memory_id"] = payload.replaces_memory_id
    source_store = get_source_record_store(request)
    if actor is not None and payload.source_refs:
        if source_store is None or resolved is None:
            raise HTTPException(status_code=503, detail="source provenance is not configured")
        try:
            fields["source_refs"] = resolve_source_refs(
                payload.source_refs,
                tenant_id=project.tenant_id,
                project_id=project.project_id,
                access_scope=resolved.effective_scope,
                source_store=source_store,
            )
        except MemoryProvenanceError as exc:
            raise HTTPException(status_code=422, detail=f"MEMORY_INVALID_PROVENANCE: {exc}") from exc
    proposal = MemoryProposal.model_validate(fields)
    try:
        created = gateway.create_memory_proposal(proposal)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    lifecycle.emit(
        LifecycleEventType.MEMORY_PROPOSED,
        project=created.project,
        payload={
            "proposal_id": str(created.id),
            "proposed_by": created.proposed_by,
            "memory_type": created.memory_type.value,
            "replaces_memory_id": (
                str(created.replaces_memory_id) if created.replaces_memory_id else None
            ),
            "memory_gateway": gateway.audit_summary(),
        },
    )
    lifecycle.emit(
        LifecycleEventType.APPROVAL_REQUESTED,
        project=created.project,
        payload={
            "proposal_id": str(created.id),
            "kind": "memory_proposal",
            "allowed_approvers": list(created.allowed_approvers),
        },
    )
    return created


@router.post(
    "/memory-proposals/{proposal_id}/decision",
    response_model=MemoryDecisionResponse,
    tags=["memory"],
)
def decide_memory_proposal(
    request: Request,
    proposal_id: UUID,
    payload: DecideMemoryProposalRequest,
    gateway: MemoryApprovalGateway = Depends(get_memory_approval_gateway),
    lifecycle: LifecycleBus = Depends(get_lifecycle_bus),
) -> MemoryDecisionResponse:
    actor = _bound_actor(request)
    if actor is not None and payload.decided_by is not None:
        raise HTTPException(status_code=422, detail="UNTRUSTED_SCOPE_FIELD")
    decided_by = payload.decided_by
    if actor is not None:
        current = gateway.get_proposal(proposal_id)
        if current is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found")
        _trusted_memory_scope(request, current.project)
        decided_by = actor.actor_id
    if decided_by is None:
        raise HTTPException(status_code=422, detail="decided_by is required for legacy requests")
    try:
        proposal, memory = gateway.decide_memory_proposal(
            proposal_id,
            approved=payload.approved,
            decided_by=decided_by,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    lifecycle.emit(
        LifecycleEventType.APPROVAL_DECIDED,
        project=proposal.project,
        payload={
            "proposal_id": str(proposal.id),
            "decision": proposal.status,
            "decided_by": decided_by,
            "memory_gateway": gateway.audit_summary(),
        },
    )
    if proposal.status == "approved":
        lifecycle.emit(
            LifecycleEventType.MEMORY_APPROVED,
            project=proposal.project,
            payload={
                "proposal_id": str(proposal.id),
                "memory_id": str(memory.id) if memory is not None else None,
                "decided_by": decided_by,
            },
        )
    elif proposal.status == "rejected":
        lifecycle.emit(
            LifecycleEventType.MEMORY_REJECTED,
            project=proposal.project,
            payload={
                "proposal_id": str(proposal.id),
                "decided_by": decided_by,
            },
        )
    return MemoryDecisionResponse(proposal=proposal, memory=memory)


@router.post(
    "/projects/{tenant_id}/{project_id}/memory-proposals",
    response_model=MemoryProposal,
    status_code=status.HTTP_201_CREATED,
    tags=["memory"],
)
def create_trusted_memory_proposal(
    request: Request,
    tenant_id: str,
    project_id: str,
    payload: CreateTrustedMemoryProposalRequest,
    gateway: MemoryApprovalGateway = Depends(get_memory_approval_gateway),
    lifecycle: LifecycleBus = Depends(get_lifecycle_bus),
) -> MemoryProposal:
    """Create a proposal from the trusted actor and project path contract."""

    actor = get_trusted_actor_context(request)
    if actor.tenant_key != tenant_id:
        raise HTTPException(status_code=403, detail="UNTRUSTED_SCOPE_FIELD")
    project = ProjectRef(tenant_id=tenant_id, project_id=project_id)
    _actor, resolved = _trusted_memory_scope(request, project)
    memory_type = payload.memory_type or infer_memory_type(payload.claim_text)
    source_refs = payload.source_refs
    if source_refs:
        source_store = get_source_record_store(request)
        if source_store is None or resolved is None:
            raise HTTPException(status_code=503, detail="source provenance is not configured")
        try:
            source_refs = resolve_source_refs(
                source_refs,
                tenant_id=tenant_id,
                project_id=project_id,
                access_scope=resolved.effective_scope,
                source_store=source_store,
            )
        except MemoryProvenanceError as exc:
            raise HTTPException(status_code=422, detail=f"MEMORY_INVALID_PROVENANCE: {exc}") from exc
    proposal = MemoryProposal(
        project=project,
        proposed_by=actor.actor_id,
        claim_text=payload.claim_text,
        claim_type=payload.claim_type,
        memory_type=memory_type,
        evidence_ids=payload.evidence_ids,
        source_refs=source_refs,
        subject=payload.subject,
        claim_slot=payload.claim_slot,
        valid_from=payload.valid_from,
        review_due_at=payload.review_due_at,
        replaces_memory_id=payload.replaces_memory_id,
        reason=payload.reason,
    )
    try:
        created = gateway.create_memory_proposal(proposal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    lifecycle.emit(
        LifecycleEventType.MEMORY_PROPOSED,
        project=created.project,
        payload={"proposal_id": str(created.id), "identity_source": actor.source},
    )
    lifecycle.emit(
        LifecycleEventType.APPROVAL_REQUESTED,
        project=created.project,
        payload={"proposal_id": str(created.id), "kind": "memory_proposal"},
    )
    return created


@router.get(
    "/memory-proposals/{proposal_id}",
    response_model=MemoryProposal,
    tags=["memory"],
)
def get_memory_proposal(
    request: Request,
    proposal_id: UUID,
    store: MemoryStore = Depends(get_memory_store),
) -> MemoryProposal:
    proposal = store.get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="MEMORY_NOT_FOUND")
    actor = _bound_actor(request)
    if actor is not None:
        if actor.tenant_key != proposal.project.tenant_id:
            raise HTTPException(status_code=403, detail="MEMORY_FORBIDDEN")
        _trusted_memory_scope(request, proposal.project)
    return proposal


@router.get(
    "/projects/{tenant_id}/{project_id}/memories",
    response_model=list[ProjectMemory],
    tags=["memory"],
)
def list_project_memories(
    request: Request,
    tenant_id: str,
    project_id: str,
    store: MemoryStore = Depends(get_memory_store),
) -> list[ProjectMemory]:
    actor, resolved = _resolve_memory_scope(request, tenant_id, project_id)
    from project_lens.context.memory_authorization import authorize_memory_candidates

    del actor
    return list(
        authorize_memory_candidates(
            store.list_memories(resolved.project),
            project=resolved.project,
            access_scope=resolved.effective_scope,
        )
    )


@router.get(
    "/projects/{tenant_id}/{project_id}/memories/search",
    tags=["memory"],
)
def search_project_memories(
    request: Request,
    tenant_id: str,
    project_id: str,
    query: str = "",
    limit: int = 5,
    subject: str | None = None,
    as_of: datetime | None = None,
    memory_type: tuple[MemoryType, ...] = (),
    retrieval: MemoryRetrievalService = Depends(get_memory_retrieval_service),
) -> dict[str, object]:
    actor, resolved = _resolve_memory_scope(request, tenant_id, project_id)
    result = retrieval.search_project_memory(
        project=resolved.project,
        access_scope=resolved.effective_scope,
        actor_id=actor.actor_id,
        chat_id=actor.chat_id,
        query=query,
        memory_types=memory_type,
        subject=subject,
        as_of=as_of,
        limit=limit,
    )
    return {
        "ok": True,
        "query": result.query,
        "retrieval_mode": result.retrieval_mode,
        "candidate_count": result.candidate_count,
        "returned_count": result.returned_count,
        "omitted_count": result.omitted_count,
        "budget_chars": result.budget_chars,
        "memories": [card.__dict__ for card in result.cards],
        "conflicts": list(result.conflicts),
        "warnings": list(result.warnings),
    }


@router.get(
    "/projects/{tenant_id}/{project_id}/memories/{memory_id}",
    tags=["memory"],
)
def get_project_memory_detail(
    request: Request,
    tenant_id: str,
    project_id: str,
    memory_id: UUID,
    as_of: datetime | None = None,
    include_history: bool = False,
    retrieval: MemoryRetrievalService = Depends(get_memory_retrieval_service),
) -> dict[str, object]:
    actor, resolved = _resolve_memory_scope(request, tenant_id, project_id)
    try:
        memory = retrieval.get_memory_detail(
            memory_id=memory_id,
            project=resolved.project,
            access_scope=resolved.effective_scope,
            allow_historical=include_history and resolved.effective_scope.allow_private_details,
            as_of=as_of,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MEMORY_NOT_FOUND") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MEMORY_FORBIDDEN") from exc
    return {
        "ok": True,
        "memory": memory.model_dump(mode="json"),
        "authorization": {
            "actor_id": actor.actor_id,
            "chat_id": actor.chat_id,
            "policy_version": resolved.effective_scope.policy_version,
        },
        "warnings": [],
    }


@router.get(
    "/projects/{tenant_id}/{project_id}/memories/{memory_id}/versions",
    tags=["memory"],
)
def list_project_memory_versions(
    request: Request,
    tenant_id: str,
    project_id: str,
    memory_id: UUID,
    retrieval: MemoryRetrievalService = Depends(get_memory_retrieval_service),
) -> dict[str, object]:
    actor, resolved = _resolve_memory_scope(request, tenant_id, project_id)
    store = retrieval.store
    memory = store.get_memory(memory_id, project=resolved.project, include_inactive=True)
    if memory is None or not memory.fact_key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MEMORY_NOT_FOUND")
    try:
        versions = tuple(
            retrieval.get_memory_detail(
                memory_id=item.id,
                project=resolved.project,
                access_scope=resolved.effective_scope,
                allow_historical=resolved.effective_scope.allow_private_details,
            )
            for item in store.list_memory_versions(resolved.project, memory.fact_key)
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MEMORY_FORBIDDEN") from exc
    return {
        "ok": True,
        "fact_key": memory.fact_key,
        "versions": [item.model_dump(mode="json") for item in versions],
        "authorization": {"actor_id": actor.actor_id, "policy_version": resolved.effective_scope.policy_version},
    }


@router.get(
    "/projects/{tenant_id}/{project_id}/memory-summary",
    tags=["memory"],
)
def get_project_memory_summary(
    request: Request,
    tenant_id: str,
    project_id: str,
    store: MemoryStore = Depends(get_memory_store),
) -> dict[str, object]:
    actor, resolved = _resolve_memory_scope(request, tenant_id, project_id)
    from project_lens.context.memory_authorization import authorize_memory_candidates

    memories = authorize_memory_candidates(
        store.list_memories(resolved.project),
        project=resolved.project,
        access_scope=resolved.effective_scope,
    )
    summary = build_memory_summary(memories)
    return {
        "ok": True,
        "entries": [entry.__dict__ for entry in summary],
        "authorization": {"actor_id": actor.actor_id, "policy_version": resolved.effective_scope.policy_version},
    }


@router.post(
    "/memories/{memory_id}/revoke",
    tags=["memory"],
)
def revoke_project_memory(
    request: Request,
    memory_id: UUID,
    payload: RevokeMemoryRequest,
    store: MemoryStore = Depends(get_memory_store),
    reviews: MemoryReviewService = Depends(get_memory_review_service),
) -> dict[str, object]:
    actor = get_trusted_actor_context(request)
    memory = store.get_memory(memory_id, include_inactive=True)
    if memory is None or memory.project.tenant_id != actor.tenant_key:
        raise HTTPException(status_code=404, detail="MEMORY_NOT_FOUND")
    _trusted_memory_scope(request, memory.project)
    try:
        revoked = store.revoke_memory(memory_id, revoked_by=actor.actor_id, reason=payload.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MEMORY_NOT_FOUND") from exc
    opened = reviews.queue.open(
        memory_id=revoked.id,
        project=revoked.project,
        reason=ReviewReason.MANUAL,
        trigger_key=f"manual:{actor.actor_id}:{revoked.updated_at.isoformat()}",
    )
    return {
        "ok": True,
        "memory": revoked.model_dump(mode="json"),
        "review_id": str(opened.review_id),
    }


@router.get(
    "/projects/{tenant_id}/{project_id}/memory-reviews",
    tags=["memory"],
)
def list_project_memory_reviews(
    request: Request,
    tenant_id: str,
    project_id: str,
    review_status: MemoryReviewStatus | None = None,
    reviews: MemoryReviewService = Depends(get_memory_review_service),
) -> dict[str, object]:
    actor, resolved = _resolve_memory_scope(request, tenant_id, project_id)
    items = reviews.list_reviews(project=resolved.project, status=review_status)
    return {
        "ok": True,
        "reviews": [item.model_dump(mode="json") for item in items],
        "authorization": {"actor_id": actor.actor_id, "policy_version": resolved.effective_scope.policy_version},
    }


@router.post(
    "/memories/{memory_id}/reviews/{review_id}/resolve",
    tags=["memory"],
)
def resolve_project_memory_review(
    request: Request,
    memory_id: UUID,
    review_id: UUID,
    payload: ResolveMemoryReviewRequest,
    reviews: MemoryReviewService = Depends(get_memory_review_service),
) -> dict[str, object]:
    actor = get_trusted_actor_context(request)
    memory = reviews.store.get_memory(memory_id, include_inactive=True)
    if memory is None or memory.project.tenant_id != actor.tenant_key:
        raise HTTPException(status_code=404, detail="MEMORY_NOT_FOUND")
    _trusted_memory_scope(request, memory.project)
    review = next(
        (item for item in reviews.queue.list(project=memory.project) if item.review_id == review_id),
        None,
    )
    if review is None or review.memory_id != memory_id:
        raise HTTPException(status_code=404, detail="MEMORY_NOT_FOUND")
    try:
        resolved = reviews.resolve_review(review_id, resolved_by=actor.actor_id, resolution=payload.resolution)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="MEMORY_NOT_FOUND") from exc
    return {"ok": True, "review": resolved.model_dump(mode="json")}
