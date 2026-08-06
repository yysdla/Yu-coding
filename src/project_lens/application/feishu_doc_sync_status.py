"""Persist and query Feishu document sync status records."""

from __future__ import annotations

from project_lens.domain.feishu_doc_sync import FeishuDocSyncStatus
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


class FeishuDocSyncStatusStore:
    def upsert(self, status: FeishuDocSyncStatus) -> FeishuDocSyncStatus:
        raise NotImplementedError

    def list_for_project(self, project: ProjectRef) -> tuple[FeishuDocSyncStatus, ...]:
        raise NotImplementedError

    def get(
        self,
        project: ProjectRef,
        doc_token: str,
    ) -> FeishuDocSyncStatus | None:
        raise NotImplementedError


class InMemoryFeishuDocSyncStatusStore(FeishuDocSyncStatusStore):
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], FeishuDocSyncStatus] = {}

    def upsert(self, status: FeishuDocSyncStatus) -> FeishuDocSyncStatus:
        key = (
            status.project.tenant_id,
            status.project.project_id,
            status.doc_token,
        )
        self._items[key] = status
        return status

    def list_for_project(self, project: ProjectRef) -> tuple[FeishuDocSyncStatus, ...]:
        items = [
            item
            for item in self._items.values()
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
        ]
        items.sort(key=lambda item: item.last_synced_at, reverse=True)
        return tuple(items)

    def get(
        self,
        project: ProjectRef,
        doc_token: str,
    ) -> FeishuDocSyncStatus | None:
        return self._items.get((project.tenant_id, project.project_id, doc_token))


class SQLiteFeishuDocSyncStatusStore(FeishuDocSyncStatusStore):
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS feishu_doc_sync_status (
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                doc_token TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (tenant_id, project_id, doc_token)
            )
            """
        )

    def upsert(self, status: FeishuDocSyncStatus) -> FeishuDocSyncStatus:
        self._database.execute(
            """
            INSERT OR REPLACE INTO feishu_doc_sync_status
            (tenant_id, project_id, doc_token, payload)
            VALUES (?, ?, ?, ?)
            """,
            (
                status.project.tenant_id,
                status.project.project_id,
                status.doc_token,
                status.model_dump_json(),
            ),
        )
        return status

    def list_for_project(self, project: ProjectRef) -> tuple[FeishuDocSyncStatus, ...]:
        rows = self._database.query_all(
            """
            SELECT payload FROM feishu_doc_sync_status
            WHERE tenant_id = ? AND project_id = ?
            """,
            (project.tenant_id, project.project_id),
        )
        items = [FeishuDocSyncStatus.model_validate_json(row["payload"]) for row in rows]
        items.sort(key=lambda item: item.last_synced_at, reverse=True)
        return tuple(items)

    def get(
        self,
        project: ProjectRef,
        doc_token: str,
    ) -> FeishuDocSyncStatus | None:
        row = self._database.query_one(
            """
            SELECT payload FROM feishu_doc_sync_status
            WHERE tenant_id = ? AND project_id = ? AND doc_token = ?
            """,
            (project.tenant_id, project.project_id, doc_token),
        )
        if row is None:
            return None
        return FeishuDocSyncStatus.model_validate_json(row["payload"])
