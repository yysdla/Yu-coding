"""Read-only operational signal contracts for ProjectOps.

Logs/metrics/traces are queried through time windows and must not be stored
as ordinary long-lived RAG documents.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from project_lens.domain.models import Evidence, ProjectRef


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OpsSignalKind(StrEnum):
    LOG = "log"
    METRIC = "metric"
    TRACE = "trace"


class OperationalSignal(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    kind: OpsSignalKind
    project: ProjectRef
    environment: str = Field(min_length=1, max_length=50)
    observed_at: datetime
    access_scope: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2_000)
    service: str | None = Field(default=None, max_length=150)
    level: str | None = Field(default=None, max_length=40)
    metric_name: str | None = Field(default=None, max_length=120)
    metric_value: float | None = None
    trace_id: str | None = Field(default=None, max_length=120)
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpsQuery(FrozenModel):
    project: ProjectRef
    start: datetime
    end: datetime
    kinds: tuple[OpsSignalKind, ...] = (
        OpsSignalKind.LOG,
        OpsSignalKind.METRIC,
        OpsSignalKind.TRACE,
    )
    environment: str | None = Field(default=None, max_length=50)
    service: str | None = Field(default=None, max_length=150)
    metric_name: str | None = Field(default=None, max_length=120)
    trace_id: str | None = Field(default=None, max_length=120)
    text_filter: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def end_must_follow_start(self) -> "OpsQuery":
        if self.end < self.start:
            raise ValueError("ops query end must not precede start")
        return self


class OpsFinding(FrozenModel):
    """Windowed ops result with ephemeral Evidence for the current answer only."""

    query: OpsQuery
    signals: tuple[OperationalSignal, ...] = ()
    summary: str = Field(default="", max_length=2_000)
    evidence: tuple[Evidence, ...] = ()
    warnings: tuple[str, ...] = ()
