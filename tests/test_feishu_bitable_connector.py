"""Feishu Bitable requirement connector tests."""

from __future__ import annotations

import pytest

from project_lens.context.connectors import FeishuBitableConnector
from project_lens.domain.models import ProjectRef


@pytest.mark.asyncio
async def test_bitable_maps_requirement_fields() -> None:
    connector = FeishuBitableConnector(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        records=(
            {
                "requirement_id": "REQ-9",
                "priority": "P1",
                "milestone": "M2",
                "acceptance_criteria": "coupon null guard",
                "version": "v1.2",
            },
            {"priority": "P0"},
            {"id": "REQ-gone", "deleted": True},
        ),
    )
    batch = await connector.sync(None)
    assert len(batch.added) == 1
    assert batch.added[0].source.source_id == "REQ-9"
    assert batch.added[0].metadata["milestone"] == "M2"
    assert batch.deleted == ("REQ-gone",)
    assert any("missing id" in warning for warning in batch.warnings)
