"""HTTP request and response schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from project_lens.context.models import TimeRange
from project_lens.domain.feishu_doc_sync import FeishuDocSyncStatus
from project_lens.domain.models import (
    AgentRun,
    ChangeImpact,
    KnowledgeGapReport,
    ProjectRef,
    ProjectSnapshot,
    RunStatus,
    TimelineEvent,
)
from project_lens.runtime.events import AgentEvent, AgentEventType


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    channel_id: str | None = Field(default=None, max_length=200)
    question: str = Field(min_length=1, max_length=20_000)


class CreateRunResponse(BaseModel):
    run_id: UUID
    trace_id: UUID
    status: RunStatus

    @classmethod
    def from_run(cls, run: AgentRun) -> "CreateRunResponse":
        return cls(run_id=run.id, trace_id=run.trace_id, status=run.status)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ProjectSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    permissions: tuple[str, ...] = ()


class ProjectSnapshotResponse(ProjectSnapshot):
    pass


class ProjectTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    permissions: tuple[str, ...] = ()
    time_range: TimeRange | None = None
    limit: int = Field(default=20, ge=1, le=50)


class ProjectTimelineResponse(BaseModel):
    events: tuple[TimelineEvent, ...] = ()


class ProjectChangeImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    permissions: tuple[str, ...] = ()
    time_range: TimeRange | None = None
    limit: int = Field(default=20, ge=1, le=50)


class ProjectChangeImpactResponse(ChangeImpact):
    pass


class ProjectKnowledgeGapsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    permissions: tuple[str, ...] = ()


class ProjectKnowledgeGapsResponse(KnowledgeGapReport):
    pass


class FeishuDocsSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    permissions: tuple[str, ...] = ()
    doc_tokens: tuple[str, ...] = ()


class FeishuDocsSyncResponse(BaseModel):
    project_id: str
    requested: int
    fetched: int
    indexed: int
    skipped_unchanged: int
    failed: tuple[str, ...] = ()
    statuses: tuple[FeishuDocSyncStatus, ...] = ()


class FeishuDocsSyncStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectRef
    user_id: str = Field(min_length=1, max_length=100)
    permissions: tuple[str, ...] = ()


class FeishuDocsSyncStatusResponse(BaseModel):
    project: ProjectRef
    statuses: tuple[FeishuDocSyncStatus, ...] = ()
    success_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0


class AgentEventResponse(BaseModel):
    run_id: UUID
    trace_id: UUID
    type: AgentEventType
    occurred_at: datetime
    payload: dict[str, Any]

    @classmethod
    def from_event(cls, event: AgentEvent) -> "AgentEventResponse":
        return cls(
            run_id=event.run_id,
            trace_id=event.trace_id,
            type=event.type,
            occurred_at=event.occurred_at,
            payload=event.payload,
        )
