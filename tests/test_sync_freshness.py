"""Connector sync orchestration, freshness, and Evidence revocation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from project_lens.application.connector_sync import ConnectorSyncService
from project_lens.application.connector_sync_status import (
    ConnectorSyncState,
    InMemoryConnectorSyncStateStore,
    freshness_warning,
    sync_state_is_stale,
)
from project_lens.context.connectors import FeishuProjectConnector, SyncCursor
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase
from project_lens.application.connector_sync_status import ConnectorSyncStateStore


def _project() -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id="payment")


@pytest.mark.asyncio
async def test_sync_backfill_then_incremental_and_revoke() -> None:
    index = InMemoryEvidenceIndex()
    store = InMemoryConnectorSyncStateStore()
    service = ConnectorSyncService(
        evidence_index=index,
        state_store=store,
        freshness_max_age_seconds=3600,
    )
    connector = FeishuProjectConnector(
        project=_project(),
        records=(
            {"id": "T1", "title": "One", "access_scope": "project:payment:read"},
            {"id": "T2", "title": "Two", "access_scope": "project:payment:read"},
        ),
    )

    first = await service.sync_connector(connector, project=_project())
    assert first.ok is True
    assert first.added_count == 2
    assert first.revoked_count == 0
    assert len(index.all()) == 2
    assert store.get("feishu_project", "demo", "payment") is not None
    assert store.get("feishu_project", "demo", "payment").cursor is not None

    # Second sync with same connector records resumes from cursor -> empty batch.
    second = await service.sync_connector(connector, project=_project())
    assert second.ok is True
    assert second.added_count == 0
    assert len(index.all()) == 2

    # New connector snapshot that deletes T1 and updates T2 content.
    connector2 = FeishuProjectConnector(
        project=_project(),
        records=(
            {"id": "T1", "deleted": True},
            {
                "id": "T2",
                "title": "Two updated",
                "access_scope": "project:payment:read",
            },
        ),
    )
    # Force cursor reset for full re-read of the new fixture stream.
    store.upsert(
        ConnectorSyncState(
            connector="feishu_project",
            tenant_id="demo",
            project_id="payment",
            cursor=SyncCursor(token="0"),
            last_attempt_at=datetime.now(timezone.utc),
            last_success_at=datetime.now(timezone.utc),
        )
    )
    third = await service.sync_connector(connector2, project=_project())
    assert third.ok is True
    assert third.revoked_count >= 1
    active = index.all()
    assert all(item.source.source_id != "T1" for item in active)
    revoked = index.all(include_revoked=True)
    assert any(item.source.source_id == "T1" and item.revoked for item in revoked)


@pytest.mark.asyncio
async def test_sync_failure_increments_error_and_keeps_cursor(tmp_path) -> None:
    class Boom:
        name = "feishu_project"

        async def health(self):
            raise RuntimeError("unused")

        async def sync(self, cursor=None):
            raise RuntimeError("rate limited")

    db = SQLiteDatabase(str(tmp_path / "sync.db"))
    store = ConnectorSyncStateStore(db)
    store.upsert(
        ConnectorSyncState(
            connector="feishu_project",
            tenant_id="demo",
            project_id="payment",
            cursor=SyncCursor(token="7"),
            last_success_at=datetime.now(timezone.utc),
        )
    )
    service = ConnectorSyncService(
        evidence_index=InMemoryEvidenceIndex(),
        state_store=store,
    )
    result = await service.sync_connector(Boom(), project=_project())
    assert result.ok is False
    assert "rate limited" in (result.error or "")
    state = store.get("feishu_project", "demo", "payment")
    assert state is not None
    assert state.cursor is not None
    assert state.cursor.token == "7"
    assert state.error_count == 1


def test_freshness_warning_when_stale() -> None:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    fresh = ConnectorSyncState(
        connector="github",
        tenant_id="demo",
        project_id="payment",
        last_success_at=now - timedelta(minutes=10),
    )
    stale = ConnectorSyncState(
        connector="github",
        tenant_id="demo",
        project_id="payment",
        last_success_at=now - timedelta(days=2),
    )
    assert sync_state_is_stale(fresh, max_age_seconds=3600, now=now) is False
    assert sync_state_is_stale(stale, max_age_seconds=3600, now=now) is True
    warning = freshness_warning(stale, max_age_seconds=3600, now=now)
    assert warning is not None
    assert "资料可能过期" in warning
    assert freshness_warning(None, max_age_seconds=3600, now=now) is not None
