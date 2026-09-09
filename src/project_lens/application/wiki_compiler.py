"""Deterministic, source-linked Wiki draft compilation for Phase 2."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from project_lens.context.authority import detect_gaps
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.context.source_store import SourceRecordStore
from project_lens.persistence.sqlite import SQLiteDatabase


class WikiPageType(StrEnum):
    OVERVIEW = "overview"
    REQUIREMENTS = "requirements"
    BITABLE = "bitable"
    MINUTES = "minutes"
    DEVELOPMENT = "development"
    TESTING = "testing"
    RELEASE = "release"
    RULES = "rules"


class WikiDraftStatus(StrEnum):
    DRAFT = "draft"
    REVIEW_REQUIRED = "review_required"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass(frozen=True)
class WikiPageDraft:
    tenant_id: str
    project_id: str
    page_type: WikiPageType
    title: str
    status: str
    content: str
    source_keys: tuple[str, ...]
    generated_at: datetime


@dataclass(frozen=True)
class WikiReviewResult:
    draft: WikiPageDraft
    published_uri: str | None = None


class WikiDraftStore:
    def put_many(self, drafts: tuple[WikiPageDraft, ...]) -> None: ...

    def list_for_project(self, tenant_id: str, project_id: str) -> tuple[WikiPageDraft, ...]: ...

    def update_status(self, tenant_id: str, project_id: str, page_type: WikiPageType, status: str) -> WikiPageDraft | None: ...


class SQLiteWikiDraftStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._db = database
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS wiki_page_drafts (
                tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, page_type TEXT NOT NULL,
                payload TEXT NOT NULL, generated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, project_id, page_type)
            )"""
        )

    def put_many(self, drafts: tuple[WikiPageDraft, ...]) -> None:
        for draft in drafts:
            self._db.execute(
                "INSERT OR REPLACE INTO wiki_page_drafts (tenant_id, project_id, page_type, payload, generated_at) VALUES (?, ?, ?, ?, ?)",
                (draft.tenant_id, draft.project_id, draft.page_type.value, json.dumps(_dump(draft), ensure_ascii=False), draft.generated_at.isoformat()),
            )

    def list_for_project(self, tenant_id: str, project_id: str) -> tuple[WikiPageDraft, ...]:
        rows = self._db.query_all(
            "SELECT payload FROM wiki_page_drafts WHERE tenant_id=? AND project_id=? ORDER BY page_type",
            (tenant_id, project_id),
        )
        return tuple(_load(json.loads(row["payload"])) for row in rows)

    def update_status(self, tenant_id: str, project_id: str, page_type: WikiPageType, status: str) -> WikiPageDraft | None:
        current = next((item for item in self.list_for_project(tenant_id, project_id) if item.page_type is page_type), None)
        if current is None:
            return None
        updated = WikiPageDraft(current.tenant_id, current.project_id, current.page_type, current.title, status, current.content, current.source_keys, current.generated_at)
        self.put_many((updated,))
        return updated


class InMemoryWikiDraftStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, WikiPageType], WikiPageDraft] = {}

    def put_many(self, drafts: tuple[WikiPageDraft, ...]) -> None:
        for draft in drafts:
            self._items[(draft.tenant_id, draft.project_id, draft.page_type)] = draft

    def list_for_project(self, tenant_id: str, project_id: str) -> tuple[WikiPageDraft, ...]:
        return tuple(
            item for (tenant, project, _), item in self._items.items()
            if tenant == tenant_id and project == project_id
        )

    def update_status(self, tenant_id: str, project_id: str, page_type: WikiPageType, status: str) -> WikiPageDraft | None:
        current = self._items.get((tenant_id, project_id, page_type))
        if current is None:
            return None
        updated = WikiPageDraft(current.tenant_id, current.project_id, current.page_type, current.title, status, current.content, current.source_keys, current.generated_at)
        self._items[(tenant_id, project_id, page_type)] = updated
        return updated


class WikiPublisher:
    def publish(self, draft: WikiPageDraft) -> str: ...


class RecordingWikiPublisher:
    """Safe local publisher used until a project-owned Feishu Wiki target is configured."""

    def __init__(self) -> None:
        self.published: list[WikiPageDraft] = []

    def publish(self, draft: WikiPageDraft) -> str:
        self.published.append(draft)
        return f"recording://wiki/{draft.tenant_id}/{draft.project_id}/{draft.page_type.value}"


class WikiDraftWorkflowService:
    """Review and publish drafts without allowing Hermes to publish directly."""

    def __init__(self, draft_store: WikiDraftStore, publisher: WikiPublisher) -> None:
        self._drafts = draft_store
        self._publisher = publisher

    def review(self, *, tenant_id: str, project_id: str, page_type: WikiPageType, approved: bool) -> WikiReviewResult:
        draft = next((item for item in self._drafts.list_for_project(tenant_id, project_id) if item.page_type is page_type), None)
        if draft is None:
            raise KeyError("Wiki draft not found")
        if draft.status == WikiDraftStatus.PUBLISHED.value:
            return WikiReviewResult(draft)
        if draft.status not in {
            WikiDraftStatus.DRAFT.value,
            WikiDraftStatus.REVIEW_REQUIRED.value,
        }:
            raise ValueError("Wiki draft is not reviewable")
        next_status = WikiDraftStatus.PUBLISHED.value if approved else WikiDraftStatus.REJECTED.value
        published_uri = self._publisher.publish(draft) if approved else None
        updated = self._drafts.update_status(tenant_id, project_id, page_type, next_status)
        if updated is None:
            raise KeyError("Wiki draft disappeared")
        return WikiReviewResult(updated, published_uri)


class WikiDraftCompiler:
    def __init__(self, source_store: SourceRecordStore, draft_store: WikiDraftStore) -> None:
        self._sources = source_store
        self._drafts = draft_store

    def compile(
        self,
        *,
        tenant_id: str,
        project_id: str,
        access_scopes: frozenset[str],
        now: datetime | None = None,
    ) -> tuple[WikiPageDraft, ...]:
        generated = now or datetime.now(timezone.utc)
        records = tuple(
            item for item in self._sources.all(tenant_id=tenant_id, project_id=project_id)
            if item.access_scope in access_scopes
        )
        gaps = detect_gaps(records, project_id=project_id, now=generated)
        grouped = {
            page: (
                records
                if page is WikiPageType.OVERVIEW
                else tuple(item for item in records if _page_for(item) is page)
            )
            for page in WikiPageType
        }
        drafts = tuple(
            _build_page(
                tenant_id=tenant_id, project_id=project_id, page_type=page,
                records=grouped[page], gaps=gaps, generated_at=generated,
            )
            for page in WikiPageType
        )
        self._drafts.put_many(drafts)
        return drafts

    def list_for_project(self, tenant_id: str, project_id: str) -> tuple[WikiPageDraft, ...]:
        return self._drafts.list_for_project(tenant_id, project_id)


def _page_for(record: SourceRecord) -> WikiPageType:
    if record.source_type is SourceType.FEISHU_BITABLE:
        return WikiPageType.BITABLE
    if record.source_type is SourceType.MEETING_MINUTE:
        return WikiPageType.MINUTES
    if record.source_type in {SourceType.GITHUB_ISSUE, SourceType.GITHUB_PULL_REQUEST, SourceType.GITHUB_COMMIT, SourceType.GITHUB_CODE, SourceType.FEISHU_PROJECT}:
        return WikiPageType.DEVELOPMENT
    topic = record.topic.casefold()
    if "test" in topic or "qa" in topic:
        return WikiPageType.TESTING
    if "release" in topic or "publish" in topic:
        return WikiPageType.RELEASE
    if "rule" in topic or "term" in topic:
        return WikiPageType.RULES
    return WikiPageType.REQUIREMENTS


def _build_page(*, tenant_id: str, project_id: str, page_type: WikiPageType, records: tuple[SourceRecord, ...], gaps: tuple, generated_at: datetime) -> WikiPageDraft:
    source_keys = tuple(":".join(item.key) for item in records)
    lines = [f"# {page_type.value}", "", "## 当前整理", ""]
    if records:
        for item in sorted(records, key=lambda value: value.observed_at, reverse=True):
            lines.append(f"- {item.title or item.source_id}: {item.content[:500]}")
    else:
        lines.append("- 当前没有授权来源。")
    lines.extend(["", "## 来源依据", ""])
    lines.extend(f"- `{':'.join(item.key)}` revision={item.revision} status={item.status} observed_at={item.observed_at.isoformat()}" for item in records)
    lines.extend(["", "## 最近更新", f"- generated_at={generated_at.isoformat()}", "", "## 关联资料", ""])
    lines.extend(f"- `{related}`" for item in records for related in item.related_sources)
    lines.extend(["", "## 冲突与待确认", ""])
    relevant = [gap for gap in gaps if gap.fact_type.value in {scope for item in records for scope in item.authority_scope}]
    lines.extend(f"- {gap.status}: {gap.reason}" for gap in relevant)
    if not relevant:
        lines.append("- 无已检测到的冲突或待确认项。")
    status = WikiDraftStatus.REVIEW_REQUIRED.value if any(gap.status in {"conflict", "unconfirmed"} for gap in relevant) else WikiDraftStatus.DRAFT.value
    return WikiPageDraft(tenant_id, project_id, page_type, f"{project_id} / {page_type.value}", status, "\n".join(lines), source_keys, generated_at)


def _dump(draft: WikiPageDraft) -> dict[str, object]:
    return {"tenant_id": draft.tenant_id, "project_id": draft.project_id, "page_type": draft.page_type.value, "title": draft.title, "status": draft.status, "content": draft.content, "source_keys": list(draft.source_keys), "generated_at": draft.generated_at.isoformat()}


def _load(payload: dict[str, object]) -> WikiPageDraft:
    return WikiPageDraft(str(payload["tenant_id"]), str(payload["project_id"]), WikiPageType(str(payload["page_type"])), str(payload["title"]), str(payload["status"]), str(payload["content"]), tuple(str(item) for item in payload.get("source_keys", [])), datetime.fromisoformat(str(payload["generated_at"])))


__all__ = ["InMemoryWikiDraftStore", "RecordingWikiPublisher", "SQLiteWikiDraftStore", "WikiDraftCompiler", "WikiDraftStatus", "WikiDraftWorkflowService", "WikiPageDraft", "WikiPageType", "WikiPublisher", "WikiReviewResult"]
