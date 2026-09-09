"""Deterministic, evidence-grounded project risk contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from project_lens.domain.models import ProjectRef


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RiskType(StrEnum):
    OVERDUE_TASK = "overdue_task"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    UNSYNCED_REQUIREMENT = "unsynced_requirement"
    CODE_STATUS_MISMATCH = "code_status_mismatch"
    PR_REVIEW_OR_CI = "pr_review_or_ci"
    CHAT_BLOCKER = "chat_blocker"


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskState(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    SNOOZED = "snoozed"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


class RiskEventType(StrEnum):
    DETECTED = "risk.detected"
    UPDATED = "risk.updated"
    SEVERITY_CHANGED = "risk.severity_changed"
    STATE_CHANGED = "risk.state_changed"
    RESOLVED = "risk.resolved"


class RiskFinding(FrozenModel):
    risk_id: str = Field(min_length=64, max_length=64)
    project: ProjectRef
    risk_type: RiskType
    severity: RiskSeverity
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=2_000)
    primary_ref: str = Field(min_length=1, max_length=500)
    owner_ids: tuple[str, ...] = ()
    affected_refs: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...]
    evidence_signature: str = Field(min_length=64, max_length=64)
    detected_at: datetime
    last_seen_at: datetime
    state: RiskState = RiskState.OPEN
    routing_queue: str | None = Field(default=None, max_length=100)
    resolved_at: datetime | None = None
    due_at: datetime | None = None

    @model_validator(mode="after")
    def validate_finding(self) -> "RiskFinding":
        if not self.evidence_ids:
            raise ValueError("risk findings require at least one evidence id")
        if tuple(sorted(set(self.owner_ids))) != self.owner_ids:
            raise ValueError("owner_ids must be unique and sorted")
        if self.last_seen_at < self.detected_at:
            raise ValueError("last_seen_at must not precede detected_at")
        if self.state == RiskState.RESOLVED and self.resolved_at is None:
            raise ValueError("resolved risks require resolved_at")
        if self.state != RiskState.RESOLVED and self.resolved_at is not None:
            raise ValueError("only resolved risks may have resolved_at")
        return self


class RiskStateEvent(FrozenModel):
    event_id: UUID = Field(default_factory=uuid4)
    risk_id: str = Field(min_length=64, max_length=64)
    event_type: RiskEventType
    occurred_at: datetime
    previous_state: RiskState | None = None
    current_state: RiskState
    previous_severity: RiskSeverity | None = None
    current_severity: RiskSeverity
    evidence_ids: tuple[UUID, ...] = ()
    actor_id: str | None = Field(default=None, max_length=200)
    details: dict[str, Any] = Field(default_factory=dict)


def stable_risk_id(
    project: ProjectRef,
    risk_type: RiskType,
    primary_ref: str,
    owner_ids: tuple[str, ...] | list[str] = (),
) -> str:
    owners = ",".join(sorted(set(owner_ids)))
    raw = f"{project.tenant_id}|{project.project_id}|{risk_type.value}|{primary_ref}|{owners}"
    return sha256(raw.encode("utf-8")).hexdigest()


def evidence_signature(items: tuple[tuple[UUID, str], ...] | list[tuple[UUID, str]]) -> str:
    raw = "|".join(f"{item_id}:{content_hash}" for item_id, content_hash in sorted(items))
    return sha256(raw.encode("utf-8")).hexdigest()


def severity_rank(severity: RiskSeverity) -> int:
    return {
        RiskSeverity.LOW: 1,
        RiskSeverity.MEDIUM: 2,
        RiskSeverity.HIGH: 3,
    }[severity]


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
