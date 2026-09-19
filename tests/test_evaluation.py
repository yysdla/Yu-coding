from pathlib import Path

from project_lens.evaluation.baseline import run_baseline
from project_lens.evaluation.retrieval import mean_reciprocal_rank, ndcg_at_k, recall_at_k
from project_lens.evaluation.run_context import evaluate


def test_retrieval_metrics() -> None:
    assert recall_at_k(["a", "b", "c"], {"b", "d"}, 2) == 0.5
    assert mean_reciprocal_rank([(["a", "b"], {"b"}), (["c"], {"c"})]) == 0.75
    assert ndcg_at_k(["a", "b"], {"b"}, 2) > 0.0


def test_demo_context_dataset_has_ten_cases_and_is_retrievable() -> None:
    root = Path(__file__).parents[1]
    result = evaluate(
        root / "examples" / "payment_service",
        root / "evaluation" / "context_dataset.json",
        k=5,
    )

    assert result["case_count"] == 10
    assert result["recall_at_k"] >= 0.8
    assert result["mrr"] >= 0.7


def test_lexical_baseline_runner_is_reproducible() -> None:
    root = Path(__file__).parents[1]
    result = run_baseline(
        root / "examples" / "payment_service",
        config_path=root / "evaluation" / "configs" / "retrieval_baseline.json",
    )

    assert result["schema_version"] == "retrieval-evaluation-report.v1"
    assert len(result["baseline_config_sha256"]) == 64
    assert result["retrieval"]["mode"] == "lexical"
    assert result["retrieval"]["vector_enabled"] is False
    assert result["report"]["case_count"] == 10
