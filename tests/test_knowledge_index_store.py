from __future__ import annotations

from datetime import datetime, timezone

from project_lens.context.knowledge_index import (
    EvidenceAdapter,
    KnowledgeIndexJob,
    KnowledgeObjectKind,
    SQLiteKnowledgeIndexStore,
)
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.persistence.sqlite import SQLiteDatabase


def _evidence(tenant: str, project: str, source: str, text: str) -> Evidence:
    return Evidence(
        type=EvidenceType.DOCUMENT,
        project=ProjectRef(tenant_id=tenant, project_id=project),
        source=SourceRef(system="docs", source_id=source),
        content=text,
        observed_at=datetime.now(timezone.utc),
        access_scope=f"project:{project}:read",
        content_hash=(text.encode("utf-8").hex() + "0" * 64)[:64],
    )


def test_store_upsert_is_idempotent_and_keeps_revisions() -> None:
    store = SQLiteKnowledgeIndexStore(SQLiteDatabase(":memory:"))
    adapter = EvidenceAdapter()
    first = adapter.adapt(_evidence("t1", "p1", "doc", "v1"))
    second = adapter.adapt(_evidence("t1", "p1", "doc", "v2"))
    assert store.upsert_documents((first, first, second)) == 3
    documents = store.list_documents(tenant_id="t1", project_id="p1")
    assert len(documents) == 2


def test_store_enforces_tenant_project_partition() -> None:
    store = SQLiteKnowledgeIndexStore(SQLiteDatabase(":memory:"))
    adapter = EvidenceAdapter()
    store.upsert_documents(
        (
            adapter.adapt(_evidence("t1", "p1", "a", "a")),
            adapter.adapt(_evidence("t2", "p1", "b", "b")),
        )
    )
    assert len(store.list_documents(tenant_id="t1", project_id="p1")) == 1
    assert len(store.list_documents(tenant_id="t2", project_id="p1")) == 1


def test_migration_is_idempotent_and_job_is_deduplicated() -> None:
    database = SQLiteDatabase(":memory:")
    first = SQLiteKnowledgeIndexStore(database)
    second = SQLiteKnowledgeIndexStore(database)
    job = KnowledgeIndexJob(
        tenant_id="t1",
        project_id="p1",
        object_kind=KnowledgeObjectKind.EVIDENCE,
        object_id="e1",
        content_hash="a" * 64,
        model_version="m1",
    )
    first.enqueue_job(job)
    second.enqueue_job(job)
    assert len(second.list_jobs(tenant_id="t1", project_id="p1")) == 1
