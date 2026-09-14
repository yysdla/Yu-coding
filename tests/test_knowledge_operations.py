from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from project_lens.application.knowledge_operations import (
    KnowledgeOperationStore,
    KnowledgeOperationsService,
)
from project_lens.application.wiki_compiler import InMemoryWikiDraftStore, WikiDraftCompiler
from project_lens.context.models import AccessContext
from project_lens.context.source_store import InMemorySourceRecordStore
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


class _Connector:
    name = "fixture"


class _Factory:
    def build(self, **kwargs):
        return (_Connector(),)


class _Sync:
    async def sync_connector(self, connector, *, project):
        return SimpleNamespace(
            connector=connector.name,
            ok=True,
            added_count=1,
            updated_count=0,
            error=None,
        )


class _Compiler:
    def compile(self, **kwargs):
        return ()


class _BrokenExport:
    def export_project(self, **kwargs):
        raise OSError("vault temporarily unavailable")


@pytest.mark.asyncio
async def test_sync_operation_is_audited_and_export_failure_isolated() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    database = SQLiteDatabase(":memory:")
    store = KnowledgeOperationStore(database)
    service = KnowledgeOperationsService(
        store=store,
        connector_sync=_Sync(),
        connector_factory=_Factory(),
        wiki_compiler=_Compiler(),
        obsidian_export=_BrokenExport(),
        obsidian_lint=None,
    )
    operation = await service.sync_sources(
        project=project,
        requested_by="u1",
        access=AccessContext(
            tenant_id="demo", user_id="u1", permissions=frozenset({"project:payment:read"})
        ),
    )
    assert operation.status == "succeeded"
    assert operation.summary["connectors"][0]["ok"] is True
    assert operation.summary["export_error"] == "vault temporarily unavailable"
    assert store.get(operation.id) == operation


def test_operation_store_preserves_project_scope(tmp_path) -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    store = KnowledgeOperationStore(SQLiteDatabase(str(tmp_path / "ops.db")))
    operation = store.create(operation_type="lint", project=project, requested_by="u1")
    loaded = store.get(operation.id)
    assert loaded is not None
    assert loaded.project == project
    assert loaded.status == "running"
