"""Connector protocol contract smoke tests."""

from __future__ import annotations

import inspect

import pytest

from project_lens.context.connectors import (
    FeishuBitableConnector,
    FeishuMinutesConnector,
    FeishuProjectConnector,
    GitHubConnector,
    SyncBatch,
    SyncCursor,
)
from project_lens.domain.models import ProjectRef


def _project() -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id="payment", service="order-service")


@pytest.mark.parametrize(
    "cls",
    [
        FeishuProjectConnector,
        FeishuBitableConnector,
        FeishuMinutesConnector,
        GitHubConnector,
    ],
)
@pytest.mark.asyncio
async def test_connector_health_and_empty_sync(cls: type) -> None:
    connector = cls(project=_project(), records=())
    health = await connector.health()
    assert health.healthy is True
    assert health.name == connector.name

    batch = await connector.sync(None)
    assert isinstance(batch, SyncBatch)
    assert batch.added == ()
    assert batch.deleted == ()
    assert batch.next_cursor is not None
    assert isinstance(batch.next_cursor, SyncCursor)


def test_github_rejects_write_methods() -> None:
    connector = GitHubConnector(project=_project(), records=())
    with pytest.raises(AttributeError, match="read-only"):
        _ = connector.create_pr
    with pytest.raises(AttributeError, match="read-only"):
        _ = connector.merge


def test_sync_methods_are_async() -> None:
    for cls in (
        FeishuProjectConnector,
        FeishuBitableConnector,
        FeishuMinutesConnector,
        GitHubConnector,
    ):
        assert inspect.iscoroutinefunction(cls.sync)
        assert inspect.iscoroutinefunction(cls.health)
