"""Review queue records for source and evidence driven memory invalidation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from project_lens.domain.memory import ReviewReason
from project_lens.domain.models import FrozenModel, ProjectRef, utc_now


class MemoryReviewStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class MemoryReview(FrozenModel):
    review_id: UUID = Field(default_factory=uuid4)
    memory_id: UUID
    project: ProjectRef
    reason: ReviewReason
    status: MemoryReviewStatus = MemoryReviewStatus.OPEN
    trigger_key: str = Field(min_length=1, max_length=300)
    opened_at: datetime = Field(default_factory=utc_now)
    due_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution: str | None = None
