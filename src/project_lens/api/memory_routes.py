"""HTTP routes for project memory proposals and approvals."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from project_lens.application.approval_harness import ApprovalError
from project_lens.application.memory_service import infer_memory_type
from project_lens.context.memory_store import MemoryStore
from project_lens.domain.memory import MemoryProposal, MemoryType, ProjectMemory
from project_lens.domain.models import ClaimType, ProjectRef
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.runtime.memory_approval_gateway import MemoryApprovalGateway

router = APIRouter()


class CreateMemoryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    proposed_by: str = Field(min_length=1, max_length=100)
    claim_text: str = Field(min_length=1, max_length=2_000)
    claim_type: ClaimType = ClaimType.FACT
    memory_type: MemoryType | None = None
    evidence_ids: tuple[UUID, ...] = ()
    reason: str = Field(default="", max_length=2_000)
    allowed_approvers: tuple[str, ...] = ()
    expires_at: datetime | None = None
    replaces_memory_id: UUID | None = None


class DecideMemoryProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    decided_by: str = Field(min_length=1, max_length=100)


class MemoryDecisionResponse(BaseModel):
    proposal: MemoryProposal
    memory: ProjectMemory | None = None


def get_memory_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


def get_memory_approval_gateway(request: Request) -> MemoryApprovalGateway:
    gateway = getattr(request.app.state, "memory_approval_gateway", None)
    if isinstance(gateway, MemoryApprovalGateway):
        return gateway
    return MemoryApprovalGateway(request.app.state.memory_store)


def get_lifecycle_bus(request: Request) -> LifecycleBus:
    bus = getattr(request.app.state, "lifecycle_bus", None)
    return bus if isinstance(bus, LifecycleBus) else LifecycleBus()


@router.post(
    "/projects/memory-proposals",
    response_model=MemoryProposal,
    status_code=status.HTTP_201_CREATED,
    tags=["memory"],
)
def create_memory_proposal(
    payload: CreateMemoryProposalRequest,
    gateway: MemoryApprovalGateway = Depends(get_memory_approval_gateway),
    lifecycle: LifecycleBus = Depends(get_lifecycle_bus),
) -> MemoryProposal:
    memory_type = payload.memory_type or infer_memory_type(payload.claim_text)
    fields: dict[str, object] = {
        "project": payload.project,
        "proposed_by": payload.proposed_by,
        "claim_text": payload.claim_text,
        "claim_type": payload.claim_type,
        "memory_type": memory_type,
        "evidence_ids": payload.evidence_ids,
        "reason": payload.reason,
        "allowed_approvers": payload.allowed_approvers,
    }
    if payload.expires_at is not None:
        fields["expires_at"] = payload.expires_at
    if payload.replaces_memory_id is not None:
        fields["replaces_memory_id"] = payload.replaces_memory_id
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
    proposal_id: UUID,
    payload: DecideMemoryProposalRequest,
    gateway: MemoryApprovalGateway = Depends(get_memory_approval_gateway),
    lifecycle: LifecycleBus = Depends(get_lifecycle_bus),
) -> MemoryDecisionResponse:
    try:
        proposal, memory = gateway.decide_memory_proposal(
            proposal_id,
            approved=payload.approved,
            decided_by=payload.decided_by,
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
            "decided_by": payload.decided_by,
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
                "decided_by": payload.decided_by,
            },
        )
    elif proposal.status == "rejected":
        lifecycle.emit(
            LifecycleEventType.MEMORY_REJECTED,
            project=proposal.project,
            payload={
                "proposal_id": str(proposal.id),
                "decided_by": payload.decided_by,
            },
        )
    return MemoryDecisionResponse(proposal=proposal, memory=memory)


@router.get(
    "/projects/{tenant_id}/{project_id}/memories",
    response_model=list[ProjectMemory],
    tags=["memory"],
)
def list_project_memories(
    tenant_id: str,
    project_id: str,
    store: MemoryStore = Depends(get_memory_store),
) -> list[ProjectMemory]:
    return list(
        store.list_memories(ProjectRef(tenant_id=tenant_id, project_id=project_id))
    )
