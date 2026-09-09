"""Task and release source records used by the task indexer.

Releases are carried as TASK evidence with metadata.kind=release to avoid
introducing EvidenceType.RELEASE before timeline/API surfaces are ready.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1)
    status: str = Field(default="open", max_length=50)
    updated_at: datetime
    access_scope: str = Field(min_length=1, max_length=200)
    service: str | None = Field(default=None, max_length=150)
    assignee: str | None = Field(default=None, max_length=100)
    related_incident_id: str | None = Field(default=None, max_length=200)
    related_commit_sha: str | None = Field(default=None, max_length=64)
    related_pr_id: str | None = Field(default=None, max_length=200)
    branch: str | None = Field(default=None, max_length=200)
    start_at: datetime | None = None
    due_at: datetime | None = None
    owner_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    dependency_status: str | None = Field(default=None, max_length=50)
    requirement_id: str | None = Field(default=None, max_length=200)
    acceptance_criteria: tuple[str, ...] = ()
    requires_code: bool = False
    url: str | None = Field(default=None, max_length=1_000)


class ReleaseRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    release_id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1)
    released_at: datetime
    access_scope: str = Field(min_length=1, max_length=200)
    service: str | None = Field(default=None, max_length=150)
    commit_shas: tuple[str, ...] = ()
    url: str | None = Field(default=None, max_length=1_000)


class TaskSource:
    """Normalize task/release payloads before indexing."""

    def normalize_task(self, record: TaskRecord) -> TaskRecord:
        if not record.summary.strip():
            raise ValueError("task summary is empty")
        if not record.access_scope.strip():
            raise ValueError("task access_scope is required")
        return record

    def normalize_release(self, record: ReleaseRecord) -> ReleaseRecord:
        if not record.summary.strip():
            raise ValueError("release summary is empty")
        if not record.access_scope.strip():
            raise ValueError("release access_scope is required")
        return record
