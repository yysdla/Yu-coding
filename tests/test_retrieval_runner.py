from datetime import datetime, timezone

from project_lens.config import Settings
from project_lens.context.engine import ContextEngine
from project_lens.context.retrieval.config import retrieval_config_from_settings
from project_lens.context.retrieval.vector import EvidenceVectorRetriever
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.evaluation.retrieval_cases import RetrievalCase
from project_lens.evaluation.retrieval_runner import (
    compare_vector_modes,
    run_retrieval_modes,
)


class FakeProvider:
    model_version = "fake-v1"

    def embed(self, texts):
        return tuple(
            (1.0, 0.0) if "payment" in text.casefold() else (0.0, 1.0)
            for text in texts
        )


def _engine(mode: str) -> ContextEngine:
    index = InMemoryEvidenceIndex()
    project = ProjectRef(tenant_id="t1", project_id="p1")
    index.add_many(
        (
            Evidence(
                type=EvidenceType.DOCUMENT,
                project=project,
                source=SourceRef(system="docs", source_id="payment.md"),
                content="Payment callback failure and retry.",
                observed_at=datetime.now(timezone.utc),
                access_scope="project:p1:read",
                content_hash="a" * 64,
            ),
            Evidence(
                type=EvidenceType.DOCUMENT,
                project=project,
                source=SourceRef(system="docs", source_id="other.md"),
                content="Unrelated catalog notes.",
                observed_at=datetime.now(timezone.utc),
                access_scope="project:p1:read",
                content_hash="b" * 64,
            ),
        )
    )
    settings = Settings(
        _env_file=None,
        knowledge_retrieval_mode=mode,
        knowledge_vector_enabled=True,
        embedding_provider="fake",
        knowledge_embedding_model="fake",
        knowledge_embedding_version="fake-v1",
    )
    return ContextEngine(
        index,
        retrieval_config=retrieval_config_from_settings(settings),
        vector_retriever=EvidenceVectorRetriever(FakeProvider()),
    )


def test_runner_reports_modes_latency_and_comparison() -> None:
    case = RetrievalCase(
        id="payment",
        tenant_id="t1",
        project_id="p1",
        query="payment callback",
        relevant_source_keys=("payment.md",),
        difficulty="simple",
    )
    runs = run_retrieval_modes(
        (case,),
        engine_factory=_engine,
        modes=("lexical", "vector", "hybrid"),
        limit=5,
    )
    assert runs["hybrid"].metrics["case_count"] == 1
    assert runs["hybrid"].metrics["p95_latency_ms"] >= 0
    assert "vector_rescue_rate" in compare_vector_modes(runs)
    assert runs["hybrid"].cases[0]["retrieved"][0]["source_id"] == "payment.md"


def test_runner_records_successful_fallback_when_vector_provider_fails() -> None:
    class FailingProvider(FakeProvider):
        def embed(self, texts):
            raise RuntimeError("provider unavailable")

    index = InMemoryEvidenceIndex()
    project = ProjectRef(tenant_id="t1", project_id="p1")
    index.add_many(
        (
            Evidence(
                type=EvidenceType.DOCUMENT,
                project=project,
                source=SourceRef(system="docs", source_id="payment.md"),
                content="Payment callback failure.",
                observed_at=datetime.now(timezone.utc),
                access_scope="project:p1:read",
                content_hash="c" * 64,
            ),
        )
    )
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
        vector_retriever=EvidenceVectorRetriever(FailingProvider()),
    )
    case = RetrievalCase(
        id="fallback",
        tenant_id="t1",
        project_id="p1",
        query="payment",
        relevant_source_keys=("payment.md",),
    )
    run = run_retrieval_modes(
        (case,),
        engine_factory=lambda _mode: engine,
        modes=("hybrid",),
    )["hybrid"]
    assert run.metrics["fallback_success_rate"] == 1.0
