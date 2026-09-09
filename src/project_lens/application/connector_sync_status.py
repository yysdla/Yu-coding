"""Durable connector sync cursors and freshness metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from project_lens.context.connectors.base import SyncCursor
from project_lens.persistence.sqlite import SQLiteDatabase


@dataclass(frozen=True)
class ConnectorSyncState:
    connector: str
    tenant_id: str
    project_id: str
    cursor: SyncCursor | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    error_count: int = 0
    last_error: str | None = None
    indexed_count: int = 0
    failed_count: int = 0


def sync_state_is_stale(
    state: ConnectorSyncState | None,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> bool:
    """True when never synced successfully or last success exceeds the freshness window."""

    if state is None or state.last_success_at is None:
        return True
    clock = now or datetime.now(timezone.utc)
    age = clock - state.last_success_at
    return age > timedelta(seconds=max(0, max_age_seconds))


def freshness_warning(
    state: ConnectorSyncState | None,
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> str | None:
    if not sync_state_is_stale(state, max_age_seconds=max_age_seconds, now=now):
        return None
    if state is None or state.last_success_at is None:
        return "资料可能过期：该数据源尚未成功同步。"
    return (
        f"资料可能过期：{state.connector} 上次成功同步于 "
        f"{state.last_success_at.isoformat()}。"
    )


class ConnectorSyncStateStore:
    """SQLite-backed sync cursor / health store."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._db = database
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_sync_states (
                connector TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (connector, tenant_id, project_id)
            )
            """
        )

    def upsert(self, state: ConnectorSyncState) -> ConnectorSyncState:
        payload = {
            "connector": state.connector,
            "tenant_id": state.tenant_id,
            "project_id": state.project_id,
            "cursor": state.cursor.token if state.cursor else None,
            "cursor_revision": state.cursor.revision if state.cursor else None,
            "last_attempt_at": (
                state.last_attempt_at.isoformat() if state.last_attempt_at else None
            ),
            "last_success_at": (
                state.last_success_at.isoformat() if state.last_success_at else None
            ),
            "error_count": state.error_count,
            "last_error": state.last_error,
            "indexed_count": state.indexed_count,
            "failed_count": state.failed_count,
        }
        self._db.execute(
            "INSERT OR REPLACE INTO connector_sync_states "
            "(connector, tenant_id, project_id, payload) VALUES (?,?,?,?)",
            (state.connector, state.tenant_id, state.project_id, json.dumps(payload)),
        )
        return state

    def get(
        self,
        connector: str,
        tenant_id: str,
        project_id: str,
    ) -> ConnectorSyncState | None:
        row = self._db.query_one(
            "SELECT payload FROM connector_sync_states "
            "WHERE connector=? AND tenant_id=? AND project_id=?",
            (connector, tenant_id, project_id),
        )
        if row is None:
            return None
        return _state_from_payload(json.loads(row["payload"]))

    def list_for_project(
        self,
        tenant_id: str,
        project_id: str,
    ) -> tuple[ConnectorSyncState, ...]:
        rows = self._db.query_all(
            "SELECT payload FROM connector_sync_states WHERE tenant_id=? AND project_id=?",
            (tenant_id, project_id),
        )
        return tuple(
            sorted(
                (_state_from_payload(json.loads(row["payload"])) for row in rows),
                key=lambda item: item.connector,
            )
        )


class InMemoryConnectorSyncStateStore:
    """Test/dev store that mirrors the SQLite contract without a database."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], ConnectorSyncState] = {}

    def upsert(self, state: ConnectorSyncState) -> ConnectorSyncState:
        key = (state.connector, state.tenant_id, state.project_id)
        self._items[key] = state
        return state

    def get(
        self,
        connector: str,
        tenant_id: str,
        project_id: str,
    ) -> ConnectorSyncState | None:
        return self._items.get((connector, tenant_id, project_id))

    def list_for_project(
        self,
        tenant_id: str,
        project_id: str,
    ) -> tuple[ConnectorSyncState, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._items.values()
                    if item.tenant_id == tenant_id and item.project_id == project_id
                ),
                key=lambda item: item.connector,
            )
        )


def _state_from_payload(payload: dict[str, object]) -> ConnectorSyncState:
    cursor_token = payload.get("cursor")
    cursor = None
    if cursor_token is not None and str(cursor_token).strip():
        revision = payload.get("cursor_revision")
        cursor = SyncCursor(
            token=str(cursor_token),
            revision=str(revision) if revision is not None else None,
        )
    return ConnectorSyncState(
        connector=str(payload["connector"]),
        tenant_id=str(payload["tenant_id"]),
        project_id=str(payload["project_id"]),
        cursor=cursor,
        last_attempt_at=_parse_dt(payload.get("last_attempt_at")),
        last_success_at=_parse_dt(payload.get("last_success_at")),
        error_count=int(payload.get("error_count") or 0),
        last_error=(
            str(payload["last_error"]) if payload.get("last_error") is not None else None
        ),
        indexed_count=int(payload.get("indexed_count") or 0),
        failed_count=int(payload.get("failed_count") or 0),
    )


def _parse_dt(raw: object) -> datetime | None:
    if raw is None or raw == "":
        return None
    return datetime.fromisoformat(str(raw))
