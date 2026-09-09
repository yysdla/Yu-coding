"""Index local task and release fixtures into TASK evidence."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from project_lens.context.indexing.common import content_hash
from project_lens.context.sources.tasks import ReleaseRecord, TaskRecord, TaskSource
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef


class TaskIndexer:
    def __init__(self, source: TaskSource | None = None) -> None:
        self._source = source or TaskSource()

    def index_tasks_file(
        self,
        path: Path,
        *,
        project: ProjectRef | None = None,
    ) -> list[Evidence]:
        payload: dict[str, Any] | list[Any] = json.loads(path.read_text(encoding="utf-8"))
        records_raw = payload if isinstance(payload, list) else payload.get("tasks", [])
        if not isinstance(records_raw, list):
            raise ValueError("tasks file must contain a list or a tasks list")
        records = [TaskRecord.model_validate(item) for item in records_raw]
        return self.index_tasks(records, project=project)

    def index_releases_file(
        self,
        path: Path,
        *,
        project: ProjectRef | None = None,
    ) -> list[Evidence]:
        payload: dict[str, Any] | list[Any] = json.loads(path.read_text(encoding="utf-8"))
        records_raw = payload if isinstance(payload, list) else payload.get("releases", [])
        if not isinstance(records_raw, list):
            raise ValueError("releases file must contain a list or a releases list")
        records = [ReleaseRecord.model_validate(item) for item in records_raw]
        return self.index_releases(records, project=project)

    def index_tasks(
        self,
        records: Iterable[TaskRecord],
        *,
        project: ProjectRef | None = None,
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        for record in records:
            try:
                normalized = self._source.normalize_task(record)
            except ValueError:
                continue
            item_project = _resolve_project(normalized, project)
            if item_project is None:
                continue
            content = _task_content(normalized)
            metadata: dict[str, Any] = {
                "kind": "task",
                "task_id": normalized.task_id,
                "title": normalized.title,
                "status": normalized.status,
                "assignee": normalized.assignee,
                "related_incident_id": normalized.related_incident_id,
                "related_commit_sha": normalized.related_commit_sha,
                "related_pr_id": normalized.related_pr_id,
                "branch": normalized.branch,
                "start_at": normalized.start_at.isoformat() if normalized.start_at else None,
                "due_at": normalized.due_at.isoformat() if normalized.due_at else None,
                "owner_ids": list(normalized.owner_ids),
                "dependency_ids": list(normalized.dependency_ids),
                "dependency_status": normalized.dependency_status,
                "requirement_id": normalized.requirement_id,
                "acceptance_criteria": list(normalized.acceptance_criteria),
                "requires_code": normalized.requires_code,
            }
            evidence.append(
                Evidence(
                    type=EvidenceType.TASK,
                    project=item_project,
                    source=SourceRef(
                        system="local_task",
                        source_id=normalized.task_id,
                        url=normalized.url,
                    ),
                    content=content,
                    observed_at=normalized.updated_at,
                    access_scope=normalized.access_scope,
                    content_hash=content_hash(
                        f"{normalized.task_id}:{normalized.status}:{content}"
                    ),
                    metadata={key: value for key, value in metadata.items() if value is not None},
                )
            )
        return evidence

    def index_releases(
        self,
        records: Iterable[ReleaseRecord],
        *,
        project: ProjectRef | None = None,
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        for record in records:
            try:
                normalized = self._source.normalize_release(record)
            except ValueError:
                continue
            item_project = _resolve_project(normalized, project)
            if item_project is None:
                continue
            content = _release_content(normalized)
            evidence.append(
                Evidence(
                    type=EvidenceType.TASK,
                    project=item_project,
                    source=SourceRef(
                        system="local_release",
                        source_id=normalized.release_id,
                        url=normalized.url,
                    ),
                    content=content,
                    observed_at=normalized.released_at,
                    access_scope=normalized.access_scope,
                    content_hash=content_hash(
                        f"{normalized.release_id}:{normalized.version}:{content}"
                    ),
                    metadata={
                        "kind": "release",
                        "release_id": normalized.release_id,
                        "version": normalized.version,
                        "title": normalized.title,
                        "commit_shas": list(normalized.commit_shas),
                    },
                )
            )
        return evidence


def _resolve_project(
    record: TaskRecord | ReleaseRecord,
    project: ProjectRef | None,
) -> ProjectRef | None:
    if project is not None:
        if record.tenant_id != project.tenant_id or record.project_id != project.project_id:
            return None
        return project.model_copy(update={"service": record.service or project.service})
    return ProjectRef(
        tenant_id=record.tenant_id,
        project_id=record.project_id,
        service=record.service,
    )


def _task_content(record: TaskRecord) -> str:
    parts = [
        f"title: {record.title}",
        f"summary: {record.summary}",
        f"status: {record.status}",
    ]
    if record.assignee:
        parts.append(f"assignee: {record.assignee}")
    if record.related_incident_id:
        parts.append(f"related_incident: {record.related_incident_id}")
    if record.related_commit_sha:
        parts.append(f"related_commit: {record.related_commit_sha}")
    if record.related_pr_id:
        parts.append(f"related_pr: {record.related_pr_id}")
    if record.branch:
        parts.append(f"branch: {record.branch}")
    if record.due_at:
        parts.append(f"due_at: {record.due_at.isoformat()}")
    if record.dependency_ids:
        parts.append(f"dependencies: {', '.join(record.dependency_ids)}")
    if record.acceptance_criteria:
        parts.append(f"acceptance_criteria: {'; '.join(record.acceptance_criteria)}")
    return "\n".join(parts)


def _release_content(record: ReleaseRecord) -> str:
    commits = ", ".join(record.commit_shas) if record.commit_shas else "(none)"
    return (
        f"title: {record.title}\n"
        f"summary: {record.summary}\n"
        f"latest release version: {record.version}\n"
        f"release commits: {commits}"
    )
