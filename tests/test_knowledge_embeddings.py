from __future__ import annotations

from datetime import datetime, timezone

from project_lens.context.knowledge_index import (
    EvidenceAdapter,
    KnowledgeChunkBuilder,
    KnowledgeEmbeddingService,
    SQLiteKnowledgeIndexStore,
)
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.persistence.sqlite import SQLiteDatabase


class FakeProvider:
    model_version = "fake-v1"

    def __init__(self, *, dimensions: int = 2, error: Exception | None = None) -> None:
        self.calls = 0
        self.dimensions = dimensions
        self.error = error

    def embed(self, texts):
        self.calls += 1
        if self.error:
            raise self.error
        return tuple(
            tuple(float(index + 1) for index in range(self.dimensions))
            for _ in texts
        )


def _chunks(text: str = "支付回调需要幂等。"):
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=ProjectRef(tenant_id="t1", project_id="p1"),
        source=SourceRef(system="docs", source_id="doc-1"),
        content=text,
        observed_at=datetime.now(timezone.utc),
        access_scope="project:p1:read",
        content_hash="a" * 64,
    )
    document = EvidenceAdapter().adapt(evidence)
    return KnowledgeChunkBuilder().build(document)


def test_embedding_cache_hit_and_content_change_recompute() -> None:
    store = SQLiteKnowledgeIndexStore(SQLiteDatabase(":memory:"))
    provider = FakeProvider()
    service = KnowledgeEmbeddingService(store, provider)
    first = _chunks()
    assert service.embed_chunks(first).completed == 1
    assert service.embed_chunks(first).cache_hits == 1
    assert provider.calls == 1

    changed = _chunks("支付回调必须幂等并记录 request id。")
    assert service.embed_chunks(changed).completed == 1
    assert provider.calls == 2


def test_model_version_isolation_causes_recompute() -> None:
    store = SQLiteKnowledgeIndexStore(SQLiteDatabase(":memory:"))
    chunks = _chunks()
    first_provider = FakeProvider()
    KnowledgeEmbeddingService(store, first_provider).embed_chunks(chunks)
    second_provider = FakeProvider()
    service = KnowledgeEmbeddingService(store, second_provider, model_version="fake-v2")

    assert service.embed_chunks(chunks).completed == 1
    assert second_provider.calls == 1


def test_invalid_provider_response_fails_without_writing_partial_vectors() -> None:
    store = SQLiteKnowledgeIndexStore(SQLiteDatabase(":memory:"))
    provider = FakeProvider(error=RuntimeError("timeout"))
    service = KnowledgeEmbeddingService(store, provider)
    chunks = _chunks()

    result = service.process_pending(chunks)

    assert result.failed == 1
    assert result.completed == 0
    assert store.get_embedding(
        tenant_id="t1",
        project_id="p1",
        chunk_id=chunks[0].chunk_id,
        model_version="fake-v1",
        content_hash=chunks[0].content_hash,
        chunker_version=chunks[0].chunker_version,
    ) is None
    assert store.list_jobs(tenant_id="t1", project_id="p1", status="failed")


def test_inconsistent_dimensions_are_rejected() -> None:
    class BadProvider(FakeProvider):
        def embed(self, texts):
            self.calls += 1
            return ((1.0, 2.0), (1.0,))

    store = SQLiteKnowledgeIndexStore(SQLiteDatabase(":memory:"))
    chunks = _chunks("a\n\nb")
    result = KnowledgeEmbeddingService(store, BadProvider()).embed_chunks(chunks)

    assert result.failed == len(chunks)
    assert result.completed == 0
