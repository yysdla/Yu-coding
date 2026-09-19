from datetime import datetime, timezone
from pathlib import Path

from project_lens.config import Settings
from project_lens.context.engine import ContextEngine
from project_lens.context.retrieval.config import retrieval_config_from_settings
from project_lens.context.retrieval.vector import EvidenceVectorRetriever
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.evaluation.release_report import build_retrieval_report
from project_lens.evaluation.retrieval_cases import RetrievalCase
from project_lens.evaluation.retrieval_runner import run_retrieval_modes
from project_lens.evaluation.security_evaluator import evaluate_retrieval_security


class Provider:
    model_version = "fake-v1"

    def embed(self, texts):
        return tuple((1.0, 0.0) for _ in texts)


def test_development_dataset_is_insufficient_for_release(tmp_path: Path) -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    index = InMemoryEvidenceIndex()
    index.add_many(
        (
            Evidence(
                type=EvidenceType.DOCUMENT,
                project=project,
                source=SourceRef(system="docs", source_id="one"),
                content="one",
                observed_at=datetime.now(timezone.utc),
                access_scope="project:p1:read",
                content_hash="a" * 64,
            ),
        )
    )

    def factory(mode: str) -> ContextEngine:
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
            vector_retriever=EvidenceVectorRetriever(Provider()),
        )

    case = RetrievalCase(
        id="one",
        tenant_id="t1",
        project_id="p1",
        query="one",
        relevant_source_keys=("one",),
    )
    runs = run_retrieval_modes((case,), engine_factory=factory)
    security = evaluate_retrieval_security(
        (case,), runs["hybrid"].cases
    )
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id":"one"}\n', encoding="utf-8")
    report = build_retrieval_report(
        project_root=Path(__file__).parents[1],
        dataset_path=dataset,
        runs=runs,
        security=security,
    )
    assert report["schema_version"] == "retrieval-evaluation-report.v2"
    assert report["gate"]["decision"] == "insufficient_data"
