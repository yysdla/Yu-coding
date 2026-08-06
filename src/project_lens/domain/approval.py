"""Approval harness contracts shared by Memory and future Apply actions."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from project_lens.domain.models import FrozenModel, ProjectRef, utc_now

DEFAULT_APPROVAL_TTL = timedelta(days=7)


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalKind(StrEnum):
    MEMORY_PROPOSAL = "memory_proposal"
    ENGINEERING_APPLY = "engineering_apply"
    CREATE_PR = "create_pr"
    RELEASE_ROLLBACK = "release_rollback"
    CONFIG_CHANGE = "config_change"
    PRODUCTION_RESTART = "production_restart"


class ApprovalRecord(FrozenModel):
    """Canonical approval audit record for harness-controlled writes."""

    approval_id: UUID = Field(default_factory=uuid4)
    kind: ApprovalKind
    proposal_id: UUID | None = None
    action_id: UUID | None = None
    project: ProjectRef
    requested_by: str = Field(min_length=1, max_length=100)
    allowed_approvers: tuple[str, ...] = ()
    decided_by: str | None = None
    decision: ApprovalStatus = ApprovalStatus.PENDING
    expires_at: datetime | None = None
    rollback_plan: str | None = Field(default=None, max_length=2_000)
    audit_events: tuple[dict[str, Any], ...] = ()
    created_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
