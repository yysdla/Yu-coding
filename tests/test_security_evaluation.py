from project_lens.evaluation.retrieval_cases import RetrievalCase
from project_lens.evaluation.security_evaluator import evaluate_retrieval_security


def test_security_evaluator_blocks_scope_and_revocation_violations() -> None:
    case = RetrievalCase(
        id="case",
        tenant_id="t1",
        project_id="p1",
        query="q",
        relevant_source_keys=("allowed",),
        forbidden_object_ids=("secret",),
    )
    result = {
        "retrieved": [
            {
                "object_id": "secret",
                "tenant_id": "t2",
                "project_id": "p2",
                "revoked": True,
            }
        ],
        "retrieval_trace": {
            "vector_candidate_count": 2,
            "authorized_candidate_count": 1,
        },
    }
    evaluation = evaluate_retrieval_security((case,), (result,))
    assert not evaluation.passed
    assert evaluation.checks["cross_tenant_leakage"] == 1
    assert evaluation.checks["forbidden_source_returned"] == 1
    assert evaluation.checks["revoked_result_used"] == 1
    assert evaluation.checks["unauthorized_scorer_input"] == 1
