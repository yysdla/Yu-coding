"""Phase 1 normalized source, sync, authority, and ACL coverage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest

from project_lens.application.connector_sync import ConnectorSyncService
from project_lens.application.connector_sync_status import InMemoryConnectorSyncStateStore
from project_lens.application.project_connector_factory import ProjectConnectorFactory
from project_lens.application.wiki_compiler import (
    InMemoryWikiDraftStore,
    WikiDraftCompiler,
    WikiPageType,
)
from project_lens.context.authority import detect_gaps, resolve_fact
from project_lens.context.connectors import FeishuDocumentConnector, FeishuProjectConnector
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.context.source_records import FactType, SourceRecord, SourceType
from project_lens.context.source_store import InMemorySourceRecordStore, SQLiteSourceRecordStore
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase
from project_lens.project_space.models import ProjectSpace, SourceConnectorRef
from project_lens.project_space.registry import ProjectRegistry


def _project(name: str = "payment") -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id=name)


@pytest.mark.asyncio
async def test_document_fixture_replay_is_version_idempotent_and_preserves_old_revision() -> None:
    project = _project()
    index = InMemoryEvidenceIndex()
    source_store = InMemorySourceRecordStore()
    state = InMemoryConnectorSyncStateStore()
    service = ConnectorSyncService(evidence_index=index, state_store=state, source_store=source_store)
    first = FeishuDocumentConnector(
        project,
        ({"id": "doc-1", "revision": "1", "content": "scope v1", "title": "Scope"},),
    )
    result1 = await service.sync_connector(first, project=project)
    assert result1.added_count == 1
    result2 = await service.sync_connector(first, project=project)
    assert result2.added_count == 0
    assert len(source_store.all(project_id="payment")) == 1

    state.upsert(replace(state.get("feishu_document", "demo", "payment"), cursor=None))
    second = FeishuDocumentConnector(
        project,
        ({"id": "doc-1", "revision": "2", "content": "scope v2", "title": "Scope"},),
    )
    result3 = await service.sync_connector(second, project=project)
    assert result3.updated_count == 1
    assert {item.revision for item in source_store.all(project_id="payment", include_revoked=True)} == {"1", "2"}
    assert len(index.all()) == 2


@pytest.mark.asyncio
async def test_failed_sync_keeps_cursor_and_retry_is_safe() -> None:
    project = _project()
    state = InMemoryConnectorSyncStateStore()
    service = ConnectorSyncService(evidence_index=InMemoryEvidenceIndex(), state_store=state)

    class Failing:
        name = "feishu_document"

        async def sync(self, cursor=None):
            raise RuntimeError("temporary upstream failure")

    failed = await service.sync_connector(Failing(), project=project)
    assert failed.ok is False
    assert failed.state.error_count == 1
    assert "temporary" in (failed.error or "")


def test_authority_prefers_formal_requirement_and_reports_conflict_stale_unconfirmed() -> None:
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    formal = SourceRecord(
        source_id="req-1", source_type=SourceType.FEISHU_DOCUMENT, project_id="payment", tenant_id="demo",
        revision="2", observed_at=now - timedelta(days=60), status="published",
        authority_scope=(FactType.REQUIREMENT_SCOPE.value,), access_scope="project:payment:read",
        content_hash="a" * 64, content="scope formal", fact_values={FactType.REQUIREMENT_SCOPE.value: "v2"},
    )
    meeting = formal.model_copy(update={
        "source_id": "meeting-1", "source_type": SourceType.MEETING_MINUTE,
        "revision": "1", "observed_at": now - timedelta(days=60), "status": "proposed",
        "content_hash": "b" * 64, "content": "scope proposed",
        "fact_values": {FactType.REQUIREMENT_SCOPE.value: "v3"},
    })
    result = resolve_fact((formal, meeting), FactType.REQUIREMENT_SCOPE, now=now)
    assert result.selected is formal
    assert result.conflicts == (meeting,)
    statuses = {gap.status for gap in detect_gaps((formal, meeting), project_id="payment", now=now)}
    assert {"conflict", "stale"}.issubset(statuses)

    unconfirmed = formal.model_copy(update={"status": "draft", "source_id": "draft-1", "revision": "3"})
    assert any(gap.status == "unconfirmed" for gap in detect_gaps((unconfirmed,), project_id="payment", now=now))


def test_acl_and_project_isolation_apply_before_source_resolution() -> None:
    from project_lens.context.engine import ContextEngine
    from project_lens.domain.models import Evidence, EvidenceType, SourceRef

    index = InMemoryEvidenceIndex()
    index.add_many(
        [
            Evidence(
                type=EvidenceType.DOCUMENT, project=_project("payment"), source=SourceRef(system="feishu_document", source_id="p"),
                content="payment scope", observed_at=datetime.now(timezone.utc), access_scope="project:payment:read",
                content_hash="c" * 64, metadata={"revision": "1", "authority_scope": ["requirement_scope"], "status": "published"},
            ),
            Evidence(
                type=EvidenceType.DOCUMENT, project=_project("crm"), source=SourceRef(system="feishu_document", source_id="c"),
                content="secret crm scope", observed_at=datetime.now(timezone.utc), access_scope="project:crm:read",
                content_hash="d" * 64, metadata={"revision": "1", "authority_scope": ["requirement_scope"]},
            ),
        ]
    )
    engine = ContextEngine(index)
    access = AccessContext(tenant_id="demo", user_id="u1", permissions=frozenset({"project:payment:read"}))
    result = engine.resolve_fact(_project("payment"), access, FactType.REQUIREMENT_SCOPE)
    assert result.selected is not None
    assert result.selected.project_id == "payment"
    assert engine.source_gaps(_project("crm"), access)  # no authorized CRM source -> missing gaps


def test_sqlite_source_store_is_append_only(tmp_path) -> None:
    store = SQLiteSourceRecordStore(SQLiteDatabase(str(tmp_path / "sources.db")))
    record = SourceRecord(
        source_id="x", source_type=SourceType.FEISHU_DOCUMENT, project_id="payment", tenant_id="demo",
        revision="1", observed_at=datetime.now(timezone.utc), access_scope="project:payment:read",
        content_hash="e" * 64, content="body",
    )
    assert store.put(record) is True
    assert store.put(record) is False
    assert store.put(record.model_copy(update={"revision": "2", "content": "new"})) is True
    assert len(store.all(project_id="payment")) == 2


def test_source_store_isolates_identical_ids_across_tenants(tmp_path) -> None:
    store = SQLiteSourceRecordStore(SQLiteDatabase(str(tmp_path / "tenant-sources.db")))
    record = SourceRecord(
        source_id="doc-1", source_type=SourceType.FEISHU_DOCUMENT, project_id="payment", tenant_id="tenant-a",
        revision="1", observed_at=datetime.now(timezone.utc), access_scope="project:payment:read",
        content_hash="f" * 64, content="tenant a",
    )
    assert store.put(record) is True
    assert store.put(record.model_copy(update={"tenant_id": "tenant-b", "content": "tenant b"})) is True
    assert len(store.all(tenant_id="tenant-a", project_id="payment")) == 1
    assert len(store.all(tenant_id="tenant-b", project_id="payment")) == 1


def test_source_store_migrates_pre_tenant_schema_without_losing_snapshot(tmp_path) -> None:
    database = SQLiteDatabase(str(tmp_path / "legacy-sources.db"))
    database.execute(
        "CREATE TABLE source_records (project_id TEXT, source_id TEXT, revision TEXT, payload TEXT, revoked INTEGER)"
    )
    record = SourceRecord(
        source_id="legacy-doc", source_type=SourceType.FEISHU_DOCUMENT,
        project_id="payment", tenant_id="demo", revision="1",
        observed_at=datetime.now(timezone.utc), access_scope="project:payment:read",
        content_hash="h" * 64, content="legacy body",
    )
    database.execute(
        "INSERT INTO source_records VALUES (?, ?, ?, ?, ?)",
        ("payment", "legacy-doc", "1", record.model_dump_json(), 0),
    )
    store = SQLiteSourceRecordStore(database)
    assert store.get("demo", "payment", "legacy-doc", "1") is not None


@pytest.mark.asyncio
async def test_project_connector_factory_replays_projectspace_fixture(tmp_path) -> None:
    fixture = tmp_path / "minutes.json"
    fixture.write_text(
        '[{"id":"minute-1","content":"confirmed scope","revision":"1"}]',
        encoding="utf-8",
    )
    space = ProjectSpace(
        tenant_id="demo",
        project_id="payment",
        display_name="Payment",
        repositories=(),
        access_scope="project:payment:read",
        source_connectors=(
            SourceConnectorRef(kind="meeting_minutes", name="minutes", path=fixture),
        ),
    )
    factory = ProjectConnectorFactory(project_registry=ProjectRegistry((space,)))
    connectors = factory.build(tenant_id="demo", project_id="payment")
    assert len(connectors) == 1
    service = ConnectorSyncService(
        evidence_index=InMemoryEvidenceIndex(),
        state_store=InMemoryConnectorSyncStateStore(),
    )
    result = await service.sync_connector(connectors[0], project=space.project)
    assert result.ok is True
    assert result.added_count == 1


def test_fact_resolution_and_source_gaps_api_are_acl_filtered() -> None:
    from fastapi.testclient import TestClient

    from project_lens.main import create_app

    app = create_app()
    app.state.source_record_store.put(
        SourceRecord(
            source_id="req-api", source_type=SourceType.FEISHU_DOCUMENT, project_id="payment", tenant_id="demo",
            revision="1", observed_at=datetime.now(timezone.utc), status="published",
            authority_scope=(FactType.REQUIREMENT_SCOPE.value,), access_scope="project:payment:read",
            content_hash="g" * 64, content="approved scope",
            fact_values={FactType.REQUIREMENT_SCOPE.value: "coupon guard"},
        )
    )
    client = TestClient(app)
    body = {
        "project": {"tenant_id": "demo", "project_id": "payment"},
        "user_id": "u1",
        "permissions": ["project:payment:read"],
        "fact_type": "requirement_scope",
    }
    resolved = client.post("/api/v1/projects/fact-resolution", json=body)
    assert resolved.status_code == 200
    assert resolved.json()["selected"]["source_id"] == "req-api"
    denied = client.post(
        "/api/v1/projects/fact-resolution",
        json={**body, "permissions": []},
    )
    assert denied.status_code == 200
    assert denied.json()["selected"] is None
    gaps = client.post(
        "/api/v1/projects/source-gaps",
        json={key: value for key, value in body.items() if key != "fact_type"},
    )
    assert gaps.status_code == 200
    assert isinstance(gaps.json()["gaps"], list)


def test_wiki_draft_compiler_keeps_source_revisions_and_review_state() -> None:
    source_store = InMemorySourceRecordStore()
    source_store.put(
        SourceRecord(
            source_id="req-1", source_type=SourceType.FEISHU_DOCUMENT,
            project_id="payment", tenant_id="demo", title="Scope", revision="2",
            observed_at=datetime.now(timezone.utc), status="published",
            authority_scope=(FactType.REQUIREMENT_SCOPE.value,),
            access_scope="project:payment:read", content_hash="i" * 64,
            content="coupon guard", fact_values={FactType.REQUIREMENT_SCOPE.value: "v2"},
        )
    )
    source_store.put(
        SourceRecord(
            source_id="meeting-1", source_type=SourceType.MEETING_MINUTE,
            project_id="payment", tenant_id="demo", title="Meeting", revision="1",
            observed_at=datetime.now(timezone.utc), status="proposed",
            authority_scope=(FactType.REQUIREMENT_SCOPE.value,),
            access_scope="project:payment:read", content_hash="j" * 64,
            content="coupon no guard", fact_values={FactType.REQUIREMENT_SCOPE.value: "v3"},
        )
    )
    compiler = WikiDraftCompiler(source_store, InMemoryWikiDraftStore())
    pages = compiler.compile(
        tenant_id="demo",
        project_id="payment",
        access_scopes=frozenset({"project:payment:read"}),
    )
    requirements = next(item for item in pages if item.page_type is WikiPageType.REQUIREMENTS)
    minutes = next(item for item in pages if item.page_type is WikiPageType.MINUTES)
    assert "demo:payment:req-1:2" in requirements.source_keys
    assert requirements.status == "review_required"
    assert "meeting-1" in minutes.content
