"""Auditable feedback and notification records for project risks."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from project_lens.domain.models import ProjectRef


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RiskFeedbackAction(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    DISMISS = "dismiss"
    SNOOZE = "snooze"
    UPDATE_PROGRESS = "update_progress"
    REQUEST_HELP = "request_help"


class RiskNotificationKind(StrEnum):
    IMMEDIATE = "immediate"
    SNOOZE_DUE = "snooze_due"
    DAILY_DIGEST = "daily_digest"
    GROUP_ESCALATION = "group_escalation"


class RiskFeedback(FrozenModel):
    feedback_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str = Field(min_length=1, max_length=300)
    risk_id: str = Field(min_length=64, max_length=64)
    project: ProjectRef
    actor_id: str = Field(min_length=1, max_length=200)
    action: RiskFeedbackAction
    reason: str = Field(default="", max_length=2_000)
    snoozed_until: datetime | None = None
    created_at: datetime
    evidence_refs: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_snooze(self) -> "RiskFeedback":
        if self.action == RiskFeedbackAction.SNOOZE and self.snoozed_until is None:
            raise ValueError("snooze feedback requires snoozed_until")
        if self.action != RiskFeedbackAction.SNOOZE and self.snoozed_until is not None:
            raise ValueError("only snooze feedback may set snoozed_until")
        return self


class RiskNotificationRecord(FrozenModel):
    notification_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str = Field(min_length=1, max_length=500)
    risk_id: str | None = Field(default=None, min_length=64, max_length=64)
    project: ProjectRef
    kind: RiskNotificationKind
    recipient_id: str = Field(min_length=1, max_length=200)
    channel_type: str = Field(pattern="^(p2p|group)$")
    sent_at: datetime
    evidence_refs: tuple[UUID, ...] = ()


class RiskFeedbackResult(FrozenModel):
    feedback: RiskFeedback
    duplicate: bool = False
    current_state: str
