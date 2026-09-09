"""Feishu Project connector mapping and deletion semantics."""

from __future__ import annotations

import pytest

from project_lens.context.connectors import FeishuProjectConnector, SyncCursor
from project_lens.domain.models import EvidenceType, ProjectRef


def _project() -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id="payment")


@pytest.mark.asyncio
async def test_maps_task_fields_and_warns_on_missing() -> None:
    connector = FeishuProjectConnector(
        project=_project(),
        records=(
            {
                "task_id": "TASK-1",
                "title": "Fix coupon",
                "status": "doing",
                "owner": "Ada",
                "due_at": "2026-09-01T00:00:00+00:00",
                "dependencies": ["TASK-0"],
                "access_scope": "project:payment:read",
            },
            {"title": "missing id"},
        ),
    )
    batch = await connector.sync(None)
    assert len(batch.added) == 1
    item = batch.added[0]
    assert item.type is EvidenceType.TASK
    assert item.source.system == "feishu_project"
    assert item.source.source_id == "TASK-1"
    assert item.metadata["owner"] == "Ada"
    assert item.metadata["dependencies"] == ["TASK-0"]
    assert batch.warnings
    assert any("missing" in warning for warning in batch.warnings)


@pytest.mark.asyncio
async def test_cursor_resume_and_deleted_records() -> None:
    connector = FeishuProjectConnector(
        project=_project(),
        records=(
            {"id": "T1", "title": "One"},
            {"id": "T2", "title": "Two"},
            {"id": "T1", "deleted": True},
        ),
    )
    first = await connector.sync(None)
    assert {item.source.source_id for item in first.added} == {"T1", "T2"}
    assert first.deleted == ("T1",)
    assert first.next_cursor is not None

    second = await connector.sync(first.next_cursor)
    assert second.added == ()
    assert second.deleted == ()

    mid = await connector.sync(SyncCursor(token="2"))
    assert mid.added == ()
    assert mid.deleted == ("T1",)
