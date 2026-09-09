"""Read-only source connector contracts and sync primitives."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from project_lens.domain.models import Evidence
from project_lens.context.source_records import SourceRecord

@dataclass(frozen=True)
class ConnectorHealth:
    healthy: bool
    name: str = ""
    message: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

@dataclass(frozen=True)
class SyncCursor:
    token: str = ""
    observed_at: datetime | None = None
    revision: str | None = None

@dataclass(frozen=True)
class SyncBatch:
    added: tuple[Evidence, ...] = ()
    updated: tuple[Evidence, ...] = ()
    records: tuple[SourceRecord, ...] = ()
    deleted: tuple[str, ...] = ()
    next_cursor: SyncCursor | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

class Connector(Protocol):
    name: str
    async def health(self) -> ConnectorHealth: ...
    async def sync(self, cursor: SyncCursor | None = None) -> SyncBatch: ...

def record_value(record: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return default
