from project_lens.evaluation.retrieval_cases import RetrievalCase
from project_lens.evaluation.retrieval_runner import RetrievalRun
from project_lens.evaluation.temporal_evaluator import evaluate_temporal_retrieval


def test_temporal_evaluator_rejects_stale_relevant_results() -> None:
    case = RetrievalCase(
        id="temporal",
        tenant_id="t1",
        project_id="p1",
        query="latest decision",
        relevant_source_keys=("decision",),
        as_of="2026-01-01T00:00:00+00:00",
    )
    run = RetrievalRun(
        mode="hybrid",
        metrics={"case_count": 1},
        cases=(
            {
                "id": "temporal",
                "retrieved": [
                    {
                        "relevant": True,
                        "stale": True,
                        "expired": False,
                        "revoked": False,
                        "status": "",
                    }
                ],
            },
        ),
        degraded_reasons=(),
    )
    result = evaluate_temporal_retrieval((case,), run)
    assert result.metrics["temporal_correctness"] == 0.0
    assert result.failures[0]["reason"] == "no_valid_relevant_result_at_as_of"
