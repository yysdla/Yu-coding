"""Context-engine query and result contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TimeRange(FrozenModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def end_must_follow_start(self) -> "TimeRange":
        if self.end < self.start:
            raise ValueError("time range end must not precede start")
        return self


class AccessContext(FrozenModel):
    tenant_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=100)
    permissions: frozenset[str] = frozenset()


class ContextQuery(FrozenModel):
    text: str = Field(min_length=1, max_length=20_000)
    project: ProjectRef
    source_types: tuple[EvidenceType, ...] = ()
    time_range: TimeRange | None = None
    limit: int = Field(default=10, ge=1, le=50)


class RetrievalHit(FrozenModel):
    evidence: Evidence
    score: float = Field(ge=0)
    channels: tuple[str, ...]
    channel_ranks: dict[str, int]


class EvidenceBundle(FrozenModel):
    query: ContextQuery
    hits: tuple[RetrievalHit, ...] = ()
    retrieval_trace: dict[str, object] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(hit.evidence for hit in self.hits)

