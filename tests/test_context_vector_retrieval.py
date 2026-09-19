from __future__ import annotations

from datetime import datetime, timezone

from project_lens.config import Settings
from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.context.retrieval.config import retrieval_config_from_settings
from project_lens.context.retrieval.vector import EvidenceVectorRetriever
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef


class FakeProvider:
    model_version = "fake-v1"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider unavailable")
        return tuple(
            (1.0, 0.0) if "支付" in text or "payment" in text.casefold() else (0.0, 1.0)
            for text in texts
        )


def _evidence(source_id: str, content: str, scope: str = "project:p1:read") -> Evidence:
    return Evidence(
        type=EvidenceType.DOCUMENT,
        project=ProjectRef(tenant_id="t1", project_id="p1"),
        source=SourceRef(system="docs", source_id=source_id),
        content=content,
        observed_at=datetime.now(timezone.utc),
        access_scope=scope,
        content_hash=(source_id.encode().hex() + "0" * 64)[:64],
    )


def _access() -> AccessContext:
    return AccessContext(
        tenant_id="t1",
        user_id="u1",
        permissions=frozenset({"project:p1:read"}),
    )


def test_vector_runs_only_after_acl_filter_and_reports_channel() -> None:
    provider = FakeProvider()
    vector = EvidenceVectorRetriever(provider)
    index = InMemoryEvidenceIndex()
    index.add_many(
        (
            _evidence("allowed", "支付回调"),
            _evidence("forbidden", "支付回调", scope="project:p1:restricted"),
        )
    )
    settings = Settings(
        _env_file=None,
        knowledge_retrieval_mode="hybrid",
        knowledge_vector_enabled=True,
        embedding_provider="fake",
        knowledge_embedding_model="fake",
        knowledge_embedding_version="fake-v1",
    )
    engine = ContextEngine(
        index,
        retrieval_config=retrieval_config_from_settings(settings),
        vector_retriever=vector,
    )

    bundle = engine.search(
        ContextQuery(
            text="payment callback",
            project=ProjectRef(tenant_id="t1", project_id="p1"),
            limit=5,
        ),
        _access(),
    )

    assert [item.evidence.source.source_id for item in bundle.hits] == ["allowed"]
    assert bundle.retrieval_trace["channel_counts"]["vector"] == 1
    assert "vector" in bundle.hits[0].channels


def test_vector_failure_falls_back_to_lexical() -> None:
    index = InMemoryEvidenceIndex()
    index.add_many((_evidence("allowed", "支付回调"),))
    settings = Settings(
        _env_file=None,
        knowledge_retrieval_mode="hybrid",
        knowledge_vector_enabled=True,
        embedding_provider="fake",
        knowledge_embedding_model="fake",
        knowledge_embedding_version="fake-v1",
        knowledge_vector_fallback=True,
    )
    engine = ContextEngine(
        index,
        retrieval_config=retrieval_config_from_settings(settings),
        vector_retriever=EvidenceVectorRetriever(FakeProvider(fail=True)),
    )

    bundle = engine.search(
        ContextQuery(
            text="支付回调",
            project=ProjectRef(tenant_id="t1", project_id="p1"),
            limit=5,
        ),
        _access(),
    )

    assert bundle.hits
    assert bundle.retrieval_trace["vector_failure"] == "RuntimeError"
    assert bundle.retrieval_trace["fallback_reason"] == "vector_retrieval_failed"
