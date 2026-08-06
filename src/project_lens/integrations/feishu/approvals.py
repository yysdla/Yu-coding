"""Approval contracts for write-capable actions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_id: UUID
    action_id: UUID
    requested_by: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    decided_at: datetime | None = None
