"""Read-only Feishu Bitable requirement connector (fixture/HTTP-ready records)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.config import assert_external_calls_allowed

from .base import ConnectorHealth, SyncBatch, SyncCursor, record_value


class FeishuBitableConnector:
    name = "feishu_bitable"

    def __init__(
        self,
        project: ProjectRef,
        records: Iterable[dict[str, Any]] = (),
        *,
        reader: "FeishuBitableReader | None" = None,
    ) -> None:
        self.project = project
        self.records = tuple(records)
        self.reader = reader

    async def health(self) -> ConnectorHealth:
        if self.reader is not None:
            return await self.reader.health()
        return ConnectorHealth(True, self.name, "read-only fixture connector")

    async def sync(self, cursor: SyncCursor | None = None) -> SyncBatch:
        start = _cursor_offset(cursor)
        next_token: str | None = None
        if self.reader is not None:
            slice_records, next_token = await self.reader.read(cursor.token if cursor else None)
        else:
            slice_records = self.records[start:]
        added: list[Evidence] = []
        deleted: list[str] = []
        warnings: list[str] = []

        for record in slice_records:
            rid = record_value(record, "id", "requirement_id", "requirementId")
            if not rid:
                warnings.append("requirement missing id")
                continue
            if bool(record_value(record, "deleted", default=False)):
                deleted.append(str(rid))
                continue
            meta = {
                "source_type": SourceType.FEISHU_BITABLE.value,
                "requirement_id": rid,
                "title": record_value(record, "title", "name", default=str(rid)),
                "priority": record_value(record, "priority"),
                "milestone": record_value(record, "milestone"),
                "acceptance_criteria": record_value(
                    record, "acceptance_criteria", "acceptanceCriteria"
                ),
                "version": record_value(record, "version"),
                "updated_at": record_value(record, "updated_at", "updatedAt"),
                "status": record_value(record, "status", default="published"),
                "owner": record_value(record, "owner", "assignee"),
                "topic": record_value(record, "topic", default="requirement"),
                "authority_scope": ("requirement_scope", "requirement_status", "owner"),
                "fact_values": {
                    "requirement_scope": str(record_value(record, "acceptance_criteria", "acceptanceCriteria", default="")),
                    "requirement_status": str(record_value(record, "status", default="")),
                    "owner": str(record_value(record, "owner", "assignee", default="")),
                },
            }
            content = str(meta)
            added.append(
                Evidence(
                    type=EvidenceType.DOCUMENT,
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

        return SyncBatch(
            added=tuple(added),
            records=tuple(SourceRecord.from_evidence(item) for item in added),
            deleted=tuple(deleted),
            next_cursor=SyncCursor(token=next_token or str(start + len(slice_records))),
            warnings=tuple(warnings),
        )


class FeishuBitableReader:
    """Small read-only OpenAPI reader for one Feishu Bitable table."""

    def __init__(
        self,
        *,
        token_provider: Any,
        app_token: str,
        table_id: str,
        base_url: str = "https://open.feishu.cn",
        page_size: int = 100,
        field_aliases: dict[str, str] | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._app_token = app_token
        self._table_id = table_id
        self._base_url = base_url.rstrip("/")
        self._page_size = max(1, min(page_size, 500))
        self._field_aliases = dict(field_aliases or {})

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(True, FeishuBitableConnector.name, "read-only Feishu Bitable API")

    async def read(self, page_token: str | None) -> tuple[tuple[dict[str, Any], ...], str | None]:
        return await asyncio.to_thread(self._read_sync, page_token)

    def _read_sync(self, page_token: str | None) -> tuple[tuple[dict[str, Any], ...], str | None]:
        assert_external_calls_allowed("feishu_bitable")
        query = f"page_size={self._page_size}"
        if page_token:
            query += f"&page_token={quote(page_token, safe='')}"
        url = f"{self._base_url}/open-apis/bitable/v1/apps/{quote(self._app_token, safe='')}/tables/{quote(self._table_id, safe='')}/records?{query}"
        request = Request(url, headers={"Authorization": f"Bearer {self._token_provider.get()}", "Content-Type": "application/json"}, method="GET")
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if int(payload.get("code", 0)) != 0:
            raise RuntimeError(f"Feishu Bitable request failed: {payload}")
        data = payload.get("data") or {}
        rows: list[dict[str, Any]] = []
        for item in data.get("items") or ():
            fields = dict(item.get("fields") or {})
            row: dict[str, Any] = {"id": item.get("record_id") or item.get("id")}
            row.update(fields)
            for canonical, remote in self._field_aliases.items():
                if remote in fields:
                    row[canonical] = fields[remote]
            rows.append(row)
        return tuple(rows), (str(data.get("page_token")) if data.get("has_more") and data.get("page_token") else None)


def _cursor_offset(cursor: SyncCursor | None) -> int:
    if cursor is None or not cursor.token.strip():
        return 0
    try:
        return max(0, int(cursor.token))
    except ValueError:
        return 0
