"""Read-only Feishu meeting minutes connector (fixture/HTTP-ready records)."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.context.source_records import SourceRecord, SourceType

from .base import ConnectorHealth, SyncBatch, SyncCursor, record_value


class FeishuMinutesConnector:
    name = "feishu_minutes"

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
            rid = record_value(record, "id", "source_id", "minute_id")
            if bool(record_value(record, "deleted", default=False)):
                if rid:
                    deleted.append(str(rid))
                else:
                    warnings.append("meeting minute missing id or content")
                continue
            body = record_value(record, "content", "text", "summary")
            if not rid or not body:
                warnings.append("meeting minute missing id or content")
                continue
            added.append(
                Evidence(
                    type=EvidenceType.DOCUMENT,
                    project=self.project,
                    source=SourceRef(system=self.name, source_id=str(rid)),
                    content=str(body),
                    observed_at=record_value(
                        record, "observed_at", default=datetime.now(timezone.utc)
                    ),
                    access_scope=str(
                        record_value(record, "access_scope", default="project")
                    ),
                    content_hash=sha256(str(body).encode()).hexdigest(),
                    metadata={
                        "source_type": SourceType.MEETING_MINUTE.value,
                        "revision": record_value(record, "revision"),
                        "cursor": record_value(record, "cursor"),
                        "warning": record_value(record, "warning"),
                        "title": record_value(record, "title", "name", default=str(rid)),
                        "owner": record_value(record, "owner", "owner_user_id"),
                        "status": record_value(record, "status", default="proposed"),
                        "topic": record_value(record, "topic", default="meeting"),
                        "authority_scope": record_value(
                            record,
                            "authority_scope",
                            default=(
                                "requirement_scope",
                                "requirement_status",
                                "development_progress",
                                "test_status",
                                "technical_decision",
                                "owner",
                            ),
                        ),
                        "fact_values": record_value(record, "fact_values", default={}),
                    },
                )
            )

        return SyncBatch(
            added=tuple(added),
            records=tuple(SourceRecord.from_evidence(item) for item in added),
            deleted=tuple(deleted),
            next_cursor=SyncCursor(token=str(start + len(slice_records))),
            warnings=tuple(warnings),
        )


def _cursor_offset(cursor: SyncCursor | None) -> int:
    if cursor is None or not cursor.token.strip():
        return 0
    try:
        return max(0, int(cursor.token))
    except ValueError:
        return 0
