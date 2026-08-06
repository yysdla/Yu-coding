"""Core domain models shared by retrieval, agent runtime, and response renderers."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceType(StrEnum):
    DOCUMENT = "document"
    CODE = "code"
    COMMIT = "commit"
    PULL_REQUEST = "pull_request"
    LOG = "log"
    TASK = "task"
    INCIDENT = "incident"
    METRIC = "metric"


class EvidenceGrade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    UNKNOWN = "unknown"


class ClaimType(StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    UNKNOWN = "unknown"


class RunStatus(StrEnum):
    ACCEPTED = "accepted"
    RESOLVING = "resolving"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    VERIFYING = "verifying"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ProjectRef(FrozenModel):
    tenant_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    service: str | None = Field(default=None, max_length=150)
    environment: str | None = Field(default=None, max_length=50)


class SourceRef(FrozenModel):
    system: str = Field(min_length=1, max_length=50)
    source_id: str = Field(min_length=1, max_length=500)
    url: str | None = None


class Evidence(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    type: EvidenceType
    project: ProjectRef
    source: SourceRef
    content: str = Field(min_length=1)
    observed_at: datetime
    access_scope: str = Field(min_length=1, max_length=200)
    content_hash: str = Field(min_length=16, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Claim(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1)
    type: ClaimType
    evidence_ids: tuple[UUID, ...] = ()
    grade: EvidenceGrade = EvidenceGrade.UNKNOWN

    @model_validator(mode="after")
    def facts_require_evidence(self) -> "Claim":
        if self.type == ClaimType.FACT and not self.evidence_ids:
            raise ValueError("factual claims require at least one evidence id")
        if self.type == ClaimType.UNKNOWN and self.grade != EvidenceGrade.UNKNOWN:
            raise ValueError("unknown claims must use the unknown evidence grade")
        return self


class ActionProposal(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    tool_name: str | None = None
    requires_approval: bool = True
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProjectSnapshot(FrozenModel):
    project: ProjectRef
    services: tuple[str, ...] = ()
    entrypoints: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    unresolved_items: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)


class TimelineEvent(FrozenModel):
    project: ProjectRef
    event_type: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    observed_at: datetime
    source: SourceRef
    evidence_id: UUID
    summary: str = Field(default="", max_length=1_000)
    references: tuple[str, ...] = ()


class ChangeImpact(FrozenModel):
    """Structured, read-only summary of changes observed in project evidence."""

    project: ProjectRef
    summary: str = Field(default="", max_length=2_000)
    affected_services: tuple[str, ...] = ()
    related_events: tuple[TimelineEvent, ...] = ()
    risks: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)


class KnowledgeGapType(StrEnum):
    ARCHITECTURE = "architecture"
    OWNER = "owner"
    VERSION_HISTORY = "version_history"
    TASK_TRACKING = "task_tracking"
    INCIDENT_REVIEW = "incident_review"
    CODE_COVERAGE = "code_coverage"
    DECISION_RECORD = "decision_record"
    ACCESS_LIMITED = "access_limited"
    API_DOC = "api_doc"
    RELEASE_HISTORY = "release_history"
    RUNBOOK = "runbook"


class KnowledgeGapSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class KnowledgeGap(FrozenModel):
    type: KnowledgeGapType
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1_000)
    recommendation: str = Field(default="", max_length=1_000)
    severity: KnowledgeGapSeverity = KnowledgeGapSeverity.MEDIUM
    source_signal: str = Field(min_length=1, max_length=200)
    evidence_ids: tuple[UUID, ...] = ()
    # Entity-aware gap targeting (service/module/incident/project).
    target_ref: str | None = Field(default=None, max_length=200)
    suggested_owners: tuple[str, ...] = ()
    # Optional GraphEvidence path summaries used when the gap is graph-derived.
    graph_summaries: tuple[str, ...] = ()


class KnowledgeGapReport(FrozenModel):
    """Structured, ACL-filtered gaps in project knowledge coverage."""

    project: ProjectRef
    gaps: tuple[KnowledgeGap, ...] = ()
    type_coverage: dict[str, int] = Field(default_factory=dict)
    # Theme-level coverage signals (0/1): owner/api_doc/runbook/release/...
    signal_coverage: dict[str, int] = Field(default_factory=dict)
    evidence_ids: tuple[UUID, ...] = ()
    generated_at: datetime = Field(default_factory=utc_now)


class GraphEvidence(FrozenModel):
    """Explainable graph path anchored to source evidence ids."""

    project: ProjectRef
    path_labels: tuple[str, ...] = ()
    relations: tuple[str, ...] = ()
    node_ids: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    summary: str = Field(default="", max_length=1_000)
    # Optional bodies so analysis can cite graph anchors outside lexical hits.
    cited_evidence: tuple[Evidence, ...] = ()


class ProjectAnswer(FrozenModel):
    project: ProjectRef
    skill: str = Field(default="project_knowledge", min_length=1, max_length=100)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str
    business_summary: str
    technical_summary: str
    claims: tuple[Claim, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    unknowns: tuple[str, ...] = ()
    recommended_actions: tuple[ActionProposal, ...] = ()

    @model_validator(mode="after")
    def claims_reference_known_evidence(self) -> "ProjectAnswer":
        known = {item.id for item in self.evidence}
        missing = {
            evidence_id
            for claim in self.claims
            for evidence_id in claim.evidence_ids
            if evidence_id not in known
        }
        if missing:
            raise ValueError(f"claims reference missing evidence: {sorted(map(str, missing))}")
        return self


class AgentRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    trace_id: UUID = Field(default_factory=uuid4)
    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    channel_id: str | None = Field(default=None, max_length=200)
    question: str = Field(min_length=1, max_length=20_000)
    status: RunStatus = RunStatus.ACCEPTED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    answer: ProjectAnswer | None = None
    error: str | None = None
