from pathlib import Path

from project_lens.evaluation.retrieval import mean_reciprocal_rank, recall_at_k
from project_lens.evaluation.run_context import evaluate


def test_retrieval_metrics() -> None:
    assert recall_at_k(["a", "b", "c"], {"b", "d"}, 2) == 0.5
    assert mean_reciprocal_rank([(["a", "b"], {"b"}), (["c"], {"c"})]) == 0.75


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
