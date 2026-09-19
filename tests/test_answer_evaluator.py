from project_lens.evaluation.answer_evaluator import evaluate_answer_citations
from project_lens.evaluation.retrieval_cases import RetrievalCase


def test_answer_evaluator_checks_correctness_completeness_and_unsupported_claims() -> None:
    case = RetrievalCase(
        id="case",
        tenant_id="t1",
        project_id="p1",
        query="q",
        relevant_object_ids=("e1",),
        expected_evidence_ids=("e1",),
    )
    result = evaluate_answer_citations(
        (case,),
        (
            {
                "id": "case",
                "retrieved_object_ids": ["e1"],
                "citations": ["e1"],
                "claims": [{"type": "fact", "evidence_ids": ["e1"]}],
            },
        ),
    )
    assert result.metrics["citation_correctness"] == 1.0
    assert result.metrics["citation_completeness"] == 1.0
    assert result.metrics["unsupported_claim_rate"] == 0.0
    assert result.metrics["abstention_precision"] == 1.0
    assert result.metrics["conflict_recall"] == 1.0


def test_answer_evaluator_scores_abstention_and_conflict() -> None:
    cases = (
        RetrievalCase(
            id="abstain",
            tenant_id="t1",
            project_id="p1",
            query="unknown",
            expected_abstention=True,
        ),
        RetrievalCase(
            id="conflict",
            tenant_id="t1",
            project_id="p1",
            query="conflicting",
            relevant_source_keys=("a",),
            expected_conflict=True,
        ),
    )
    result = evaluate_answer_citations(
        cases,
        (
            {"id": "abstain", "abstained": True},
            {"id": "conflict", "conflict_detected": True},
        ),
    )
    assert result.metrics["abstention_recall"] == 1.0
    assert result.metrics["conflict_recall"] == 1.0
