"""Read-only Feishu Project task connector (fixture/HTTP-ready records)."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.context.source_records import SourceRecord, SourceType

from .base import ConnectorHealth, SyncBatch, SyncCursor, record_value


class FeishuProjectConnector:
    name = "feishu_project"

    def __init__(
        self,
        project: ProjectRef,
        records: Iterable[dict[str, Any]] = (),
    ) -> None:
        self.project = project
        self.records = tuple(records)

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(True, self.name, "read-only fixture connector")

    async def sync(self, cursor: SyncCursor | None = None) -> SyncBatch:
        start = _cursor_offset(cursor)
        slice_records = self.records[start:]
        added: list[Evidence] = []
        deleted: list[str] = []
        warnings: list[str] = []

        for record in slice_records:
            rid = record_value(record, "id", "task_id", "taskId")
            if not rid:
                warnings.append("task missing id or title")
                continue
            if bool(record_value(record, "deleted", default=False)):
                deleted.append(str(rid))
                continue
            title = record_value(record, "title", "name")
            if not title:
                warnings.append("task missing id or title")
                continue
            meta = {
                "source_type": SourceType.FEISHU_PROJECT.value,
                "task_id": rid,
                "title": title,
                "status": record_value(record, "status"),
                "owner": record_value(record, "owner", "assignee"),
                "start_at": record_value(record, "start_at", "startAt"),
                "due_at": record_value(record, "due_at", "dueAt"),
                "updated_at": record_value(record, "updated_at", "updatedAt"),
                "dependencies": record_value(record, "dependencies", default=()),
                "revision": record_value(record, "revision", "version", "updated_at", "updatedAt"),
                "topic": record_value(record, "topic", default="project_plan"),
                "authority_scope": ("requirement_status", "owner", "test_status"),
                "fact_values": {
                    "requirement_status": str(record_value(record, "status", default="")),
                    "test_status": str(record_value(record, "test_status", default="")),
                    "owner": str(record_value(record, "owner", "assignee", default="")),
                },
            }
            content = str(meta)
            added.append(
                Evidence(
                    type=EvidenceType.TASK,
                    project=self.project,
                    source=SourceRef(system=self.name, source_id=str(rid)),
                    content=content,
                    observed_at=datetime.now(timezone.utc),
                    access_scope=str(
                        record_value(record, "access_scope", default="project")
                    ),
                    content_hash=sha256(content.encode()).hexdigest(),
                    metadata=meta,
                )
            )

        next_token = str(start + len(slice_records))
        return SyncBatch(
            added=tuple(added),
            records=tuple(SourceRecord.from_evidence(item) for item in added),
            deleted=tuple(deleted),
            next_cursor=SyncCursor(token=next_token),
            warnings=tuple(warnings),
        )


def _cursor_offset(cursor: SyncCursor | None) -> int:
    if cursor is None or not cursor.token.strip():
        return 0
    try:
        return max(0, int(cursor.token))
    except ValueError:
        return 0
