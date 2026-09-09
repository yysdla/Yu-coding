"""GitHub read-only connector tests."""

from __future__ import annotations

import pytest

from project_lens.context.connectors import GitHubConnector
from project_lens.domain.models import EvidenceType, ProjectRef


@pytest.mark.asyncio
async def test_github_maps_commit_pr_and_rejects_writes() -> None:
    connector = GitHubConnector(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        records=(
            {"kind": "commit", "sha": "abc123", "message": "fix coupon"},
            {
                "kind": "pull_request",
                "number": 42,
                "title": "hotfix",
                "html_url": "https://github.example/pr/42",
            },
            {"kind": "check_run", "id": "ci-1", "conclusion": "failure"},
            {"kind": "branch", "name": "main"},
            {"id": "abc123", "deleted": True},
        ),
    )
    batch = await connector.sync(None)
    kinds = {item.metadata["kind"]: item.type for item in batch.added}
    assert kinds["commit"] is EvidenceType.COMMIT
    assert kinds["pull_request"] is EvidenceType.PULL_REQUEST
    assert kinds["check_run"] is EvidenceType.LOG
    assert kinds["branch"] is EvidenceType.CODE
    assert batch.deleted == ("abc123",)

    with pytest.raises(AttributeError, match="read-only"):
        _ = connector.close_issue
