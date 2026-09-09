"""Read-only GitHub connector (fixture/HTTP-ready records). Write APIs are rejected."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.config import assert_external_calls_allowed

from .base import ConnectorHealth, SyncBatch, SyncCursor, record_value

_KINDS: dict[str, EvidenceType] = {
    "commit": EvidenceType.COMMIT,
    "pull_request": EvidenceType.PULL_REQUEST,
    "pr": EvidenceType.PULL_REQUEST,
    "review": EvidenceType.PULL_REQUEST,
    "check_run": EvidenceType.LOG,
    "branch": EvidenceType.CODE,
    "repository": EvidenceType.CODE,
    "issue": EvidenceType.TASK,
}

_WRITE_METHODS = frozenset(
    {"merge", "write_comment", "create_pr", "close_issue", "create_issue"}
)


class GitHubConnector:
    name = "github"

    def __init__(
        self,
        project: ProjectRef,
        records: Iterable[dict[str, Any]] = (),
        *,
        reader: "GitHubReadClient | None" = None,
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
            rid = record_value(record, "id", "number", "sha", "name")
            if not rid:
                warnings.append("github record missing id")
                continue
            if bool(record_value(record, "deleted", default=False)):
                deleted.append(str(rid))
                continue
            kind = str(record_value(record, "kind", "type", default="repository"))
            content = str(record)
            source_type = {
                "commit": SourceType.GITHUB_COMMIT.value,
                "pull_request": SourceType.GITHUB_PULL_REQUEST.value,
                "pr": SourceType.GITHUB_PULL_REQUEST.value,
                "issue": SourceType.GITHUB_ISSUE.value,
            }.get(kind, SourceType.GITHUB_CODE.value)
            added.append(
                Evidence(
                    type=_KINDS.get(kind, EvidenceType.CODE),
                    project=self.project,
                    source=SourceRef(
                        system=self.name,
                        source_id=str(rid),
                        url=record_value(record, "url", "html_url"),
                    ),
                    content=content,
                    observed_at=record_value(
                        record, "observed_at", default=datetime.now(timezone.utc)
                    ),
                    access_scope=str(
                        record_value(record, "access_scope", default="project")
                    ),
                    content_hash=sha256(content.encode()).hexdigest(),
                    metadata={
                        "kind": kind,
                        "references": record_value(record, "references", default=()),
                        "source_type": source_type,
                        "revision": record_value(record, "updated_at", "updatedAt", "sha", "id", default=str(rid)),
                        "title": record_value(record, "title", "message", "name", default=str(rid)),
                        "owner": record_value(record, "owner", "author", "assignee"),
                        "status": record_value(record, "status", "state", "conclusion", default="observed"),
                        "topic": record_value(record, "topic", default="development"),
                        "authority_scope": record_value(
                            record,
                            "authority_scope",
                            default=("development_progress",),
                        ),
                        "fact_values": {
                            "development_progress": str(
                                record_value(record, "status", "state", "conclusion", default="")
                            )
                        },
                    },
                )
            )

        return SyncBatch(
            added=tuple(added),
            records=tuple(SourceRecord.from_evidence(item) for item in added),
            deleted=tuple(deleted),
            next_cursor=SyncCursor(token=next_token or str(start + len(slice_records))),
            warnings=tuple(warnings),
        )

    def __getattr__(self, name: str) -> Any:
        if name in _WRITE_METHODS:
            raise AttributeError(f"GitHub connector is read-only: {name}")
        raise AttributeError(name)


class GitHubReadClient:
    """Read issues, pull requests, commits, and branches from one repository."""

    def __init__(
        self,
        *,
        repository: str,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        page_size: int = 100,
    ) -> None:
        self._repository = repository.strip("/")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._page_size = max(1, min(page_size, 100))

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(True, GitHubConnector.name, "read-only GitHub API")

    async def read(self, cursor: str | None) -> tuple[tuple[dict[str, Any], ...], str | None]:
        # The GitHub API does not expose one cursor spanning heterogeneous
        # resources. Revision idempotency makes each complete read safe.
        return await asyncio.to_thread(self._read_sync)

    def _read_sync(self) -> tuple[tuple[dict[str, Any], ...], str | None]:
        assert_external_calls_allowed("github")
        records: list[dict[str, Any]] = []
        for endpoint, kind in (
            ("issues", "issue"),
            ("pulls", "pull_request"),
            ("commits", "commit"),
            ("branches", "branch"),
        ):
            for item in self._get_list(endpoint):
                # GitHub's issues endpoint also returns pull requests.
                if kind == "issue" and item.get("pull_request"):
                    continue
                row = dict(item)
                row["kind"] = kind
                if kind == "commit":
                    row["id"] = f"commit:{item.get('sha', '')}"
                    row["message"] = ((item.get("commit") or {}).get("message"))
                    row["updated_at"] = ((item.get("commit") or {}).get("author") or {}).get("date")
                elif kind == "branch":
                    row["id"] = f"branch:{item.get('name', '')}"
                    row["sha"] = ((item.get("commit") or {}).get("sha"))
                else:
                    row["id"] = f"{kind}:{item.get('number') or item.get('id', '')}"
                    row["updated_at"] = row.get("updated_at") or row.get("created_at")
                records.append(row)
        return tuple(records), None

    def _get_list(self, endpoint: str) -> tuple[dict[str, Any], ...]:
        query = urlencode({"state": "all", "per_page": self._page_size})
        if endpoint in {"commits", "branches"}:
            query = urlencode({"per_page": self._page_size})
        url = f"{self._base_url}/repos/{self._repository}/{endpoint}?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(url, headers=headers, method="GET")
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError(f"GitHub request failed for {endpoint}: {payload}")
        return tuple(item for item in payload if isinstance(item, dict))


def _cursor_offset(cursor: SyncCursor | None) -> int:
    if cursor is None or not cursor.token.strip():
        return 0
    try:
        return max(0, int(cursor.token))
    except ValueError:
        return 0
