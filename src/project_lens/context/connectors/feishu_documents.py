"""Read-only Feishu document connector for fixture and adapter-backed records."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable

from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef

from .base import ConnectorHealth, SyncBatch, SyncCursor, record_value


class FeishuDocumentConnector:
    name = "feishu_document"

    def __init__(self, project: ProjectRef, records: Iterable[dict[str, Any]] = ()) -> None:
        self.project = project
        self.records = tuple(records)

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(True, self.name, "read-only fixture connector")

    async def sync(self, cursor: SyncCursor | None = None) -> SyncBatch:
        start = _cursor_offset(cursor)
        rows = self.records[start:]
        added: list[Evidence] = []
        deleted: list[str] = []
        warnings: list[str] = []
        for record in rows:
            source_id = record_value(record, "source_id", "id", "doc_token", "document_id")
            if not source_id:
                warnings.append("feishu document missing source id")
                continue
            if bool(record_value(record, "deleted", default=False)):
                deleted.append(str(source_id))
                continue
            content = str(record_value(record, "content", "text", default="")).strip()
            if not content:
                warnings.append(f"feishu document {source_id} missing content")
                continue
            revision = str(record_value(record, "revision", "revision_id", "version", default="1"))
            observed_at = _datetime(record_value(record, "observed_at", "updated_at"))
            metadata = {
                "source_type": SourceType.FEISHU_DOCUMENT.value,
                "title": str(record_value(record, "title", "name", default="")),
                "revision": revision,
                "owner": record_value(record, "owner", "owner_user_id"),
                "status": record_value(record, "status", default="published"),
                "topic": record_value(record, "topic", default="requirements"),
                "authority_scope": record_value(record, "authority_scope", default=("requirement_scope",)),
                "fact_values": record_value(record, "fact_values", default={}),
            }
            added.append(
                Evidence(
                    type=EvidenceType.DOCUMENT,
                    project=self.project,
                    source=SourceRef(
                        system=self.name,
                        source_id=str(source_id),
                        url=record_value(record, "raw_uri", "url", "doc_url"),
                    ),
                    content=content,
                    observed_at=observed_at,
                    access_scope=str(record_value(record, "access_scope", default=f"project:{self.project.project_id}:read")),
                    content_hash=sha256(content.encode("utf-8")).hexdigest(),
                    metadata=metadata,
                )
            )
        return SyncBatch(
            added=tuple(added),
            records=tuple(SourceRecord.from_evidence(item) for item in added),
            deleted=tuple(deleted),
            next_cursor=SyncCursor(token=str(start + len(rows))),
            warnings=tuple(warnings),
        )


def _cursor_offset(cursor: SyncCursor | None) -> int:
    if cursor is None or not cursor.token.strip():
        return 0
    try:
        return max(0, int(cursor.token))
    except ValueError:
        return 0


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


__all__ = ["FeishuDocumentConnector"]
