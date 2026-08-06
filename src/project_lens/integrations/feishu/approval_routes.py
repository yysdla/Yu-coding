"""Approval endpoints used by Feishu card actions and internal clients."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from project_lens.integrations.feishu.approvals import ApprovalRequest
from project_lens.persistence.sqlite import SQLiteApprovalStore

router = APIRouter()


class CreateApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    requested_by: str = Field(min_length=1, max_length=100)


class DecideApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    decided_by: str = Field(min_length=1, max_length=100)


def get_approval_store(request: Request) -> SQLiteApprovalStore:
    return request.app.state.approval_store


@router.post(
    "/runs/{run_id}/approvals",
    response_model=ApprovalRequest,
    status_code=status.HTTP_201_CREATED,
    tags=["approvals"],
)
def create_approval(
    run_id: UUID,
    payload: CreateApprovalRequest,
    request: Request,
    store: SQLiteApprovalStore = Depends(get_approval_store),
) -> ApprovalRequest:
    if request.app.state.run_service.get(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    return store.create(
        run_id=run_id,
        action_id=payload.action_id,
        requested_by=payload.requested_by,
    )


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalRequest,
    tags=["approvals"],
)
def decide_approval(
    approval_id: UUID,
    payload: DecideApprovalRequest,
    store: SQLiteApprovalStore = Depends(get_approval_store),
) -> ApprovalRequest:
    try:
        result = store.decide(
            approval_id,
            approved=payload.approved,
            decided_by=payload.decided_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return result
