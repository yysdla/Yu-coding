"""Audited orchestration for ProjectLens knowledge maintenance operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import uuid4

from project_lens.application.connector_sync import ConnectorSyncService
from project_lens.application.project_connector_factory import ProjectConnectorFactory
from project_lens.application.wiki_compiler import WikiDraftCompiler
from project_lens.context.models import AccessContext
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.exporter import ObsidianExportService
from project_lens.obsidian.lint import ObsidianLintService
from project_lens.persistence.sqlite import SQLiteDatabase


@dataclass(frozen=True)
class KnowledgeOperation:
    id: str
    operation_type: str
    project: ProjectRef
    requested_by: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    summary: dict[str, object] | None = None
    error: str | None = None
    audit_refs: tuple[str, ...] = ()


class KnowledgeOperationStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._db = database
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_operations (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                payload TEXT NOT NULL
            )"""
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_operations_project ON knowledge_operations (tenant_id, project_id)"
        )

    def create(self, *, operation_type: str, project: ProjectRef, requested_by: str) -> KnowledgeOperation:
        operation = KnowledgeOperation(
            id=str(uuid4()),
            operation_type=operation_type,
            project=project,
            requested_by=requested_by,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        self.save(operation)
        return operation

    def save(self, operation: KnowledgeOperation) -> KnowledgeOperation:
        self._db.execute(
            "INSERT OR REPLACE INTO knowledge_operations (id, tenant_id, project_id, operation_type, payload) VALUES (?, ?, ?, ?, ?)",
            (operation.id, operation.project.tenant_id, operation.project.project_id, operation.operation_type, json.dumps(_dump(operation), ensure_ascii=False)),
        )
        return operation

    def get(self, operation_id: str) -> KnowledgeOperation | None:
        row = self._db.query_one("SELECT payload FROM knowledge_operations WHERE id = ?", (operation_id,))
        return _load(json.loads(row["payload"])) if row else None


class KnowledgeOperationsService:
    """Separate source sync, export, and lint into auditable failure-isolated operations."""

    def __init__(
        self,
        *,
        store: KnowledgeOperationStore,
        connector_sync: ConnectorSyncService,
        connector_factory: ProjectConnectorFactory,
        wiki_compiler: WikiDraftCompiler,
        obsidian_export: ObsidianExportService | None,
        obsidian_lint: ObsidianLintService | None,
    ) -> None:
        self._store = store
        self._sync = connector_sync
        self._factory = connector_factory
        self._compiler = wiki_compiler
        self._export = obsidian_export
        self._lint = obsidian_lint

    def get(self, operation_id: str) -> KnowledgeOperation | None:
        return self._store.get(operation_id)

    async def sync_sources(
        self,
        *,
        project: ProjectRef,
        requested_by: str,
        access: AccessContext,
        connector_names: tuple[str, ...] = (),
        compile_wiki: bool = True,
        export_reviewed: bool = True,
        dry_run: bool = False,
    ) -> KnowledgeOperation:
        operation = self._store.create(operation_type="sync", project=project, requested_by=requested_by)
        try:
            connectors = self._factory.build(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
                names=connector_names,
            )
            if dry_run:
                return self._finish(operation, {"dry_run": True, "connectors": [item.name for item in connectors]})
            results_list = []
            for connector in connectors:
                results_list.append(await self._sync.sync_connector(connector, project=project))
            results = tuple(results_list)
            drafts = ()
            if compile_wiki:
                drafts = self._compiler.compile(
                    tenant_id=project.tenant_id,
                    project_id=project.project_id,
                    access_scopes=access.permissions,
                )
            export_id = None
            export_error = None
            if export_reviewed and self._export is not None:
                try:
                    exported = self._export.export_project(project=project, access=access)
                    export_id = exported.export_id
                except Exception as exc:  # noqa: BLE001 - file boundary is isolated from Evidence
                    export_error = str(exc)
            summary = {
                "connectors": [
                    {"name": item.connector, "ok": item.ok, "added": item.added_count, "updated": item.updated_count, "error": item.error}
                    for item in results
                ],
                "compiled_wiki_pages": len(drafts),
                "export_id": export_id,
                "export_error": export_error,
            }
            return self._finish(operation, summary, audit_refs=tuple(item.connector for item in results))
        except Exception as exc:  # noqa: BLE001 - operation boundary
            return self._fail(operation, str(exc))

    def export_obsidian(
        self,
        *,
        project: ProjectRef,
        requested_by: str,
        access: AccessContext,
        dry_run: bool = False,
    ) -> KnowledgeOperation:
        operation = self._store.create(operation_type="export", project=project, requested_by=requested_by)
        if self._export is None:
            return self._fail(operation, "Obsidian export is not configured")
        try:
            if dry_run:
                return self._finish(operation, {"dry_run": True, "project": project.project_id})
            result = self._export.export_project(project=project, access=access)
            return self._finish(operation, {"export_id": result.export_id, "exported": len(result.exported_paths), "skipped": len(result.skipped_paths)})
        except Exception as exc:  # noqa: BLE001
            return self._fail(operation, str(exc))

    def lint_wiki(self, *, project: ProjectRef, requested_by: str, dry_run: bool = False) -> KnowledgeOperation:
        operation = self._store.create(operation_type="lint", project=project, requested_by=requested_by)
        if self._lint is None:
            return self._fail(operation, "Obsidian lint is not configured")
        try:
            findings = self._lint.lint(project=project)
            review_path = None if dry_run else self._lint.write_review(project=project, findings=findings)
            return self._finish(operation, {"dry_run": dry_run, "finding_count": len(findings), "review_path": review_path})
        except Exception as exc:  # noqa: BLE001
            return self._fail(operation, str(exc))

    def _finish(self, operation: KnowledgeOperation, summary: dict[str, object], audit_refs: tuple[str, ...] = ()) -> KnowledgeOperation:
        return self._store.save(replace(operation, status="succeeded", finished_at=datetime.now(timezone.utc), summary=summary, audit_refs=audit_refs))

    def _fail(self, operation: KnowledgeOperation, error: str) -> KnowledgeOperation:
        return self._store.save(replace(operation, status="failed", finished_at=datetime.now(timezone.utc), error=error))


def _dump(operation: KnowledgeOperation) -> dict[str, object]:
    return {
        "id": operation.id,
        "operation_type": operation.operation_type,
        "project": operation.project.model_dump(),
        "requested_by": operation.requested_by,
        "status": operation.status,
        "started_at": operation.started_at.isoformat(),
        "finished_at": operation.finished_at.isoformat() if operation.finished_at else None,
        "summary": operation.summary,
        "error": operation.error,
        "audit_refs": list(operation.audit_refs),
    }


def _load(payload: dict[str, object]) -> KnowledgeOperation:
    return KnowledgeOperation(
        id=str(payload["id"]), operation_type=str(payload["operation_type"]),
        project=ProjectRef.model_validate(payload["project"]), requested_by=str(payload["requested_by"]),
        status=str(payload["status"]), started_at=datetime.fromisoformat(str(payload["started_at"])),
        finished_at=datetime.fromisoformat(str(payload["finished_at"])) if payload.get("finished_at") else None,
        summary=dict(payload["summary"]) if isinstance(payload.get("summary"), dict) else None,
        error=str(payload["error"]) if payload.get("error") else None,
        audit_refs=tuple(str(item) for item in payload.get("audit_refs", ())),
    )
