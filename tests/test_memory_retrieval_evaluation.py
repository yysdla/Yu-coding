from uuid import uuid4

from project_lens.domain.memory import MemoryType, ProjectMemory
from project_lens.domain.models import ProjectRef
from project_lens.evaluation.memory_retrieval import (
    HistoryRetrievalCase,
    MemoryRetrievalCase,
    duplicate_card_rate,
    evaluate_history_retrieval,
    evaluate_memory_retrieval,
    ndcg_at_k,
)


def _memory(text: str) -> ProjectMemory:
    return ProjectMemory(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        text=text,
        memory_type=MemoryType.RISK,
        evidence_ids=(uuid4(),),
        approved_by="manager",
    )


def test_ndcg_and_duplicate_rate_are_bounded() -> None:
    assert ndcg_at_k(["a", "b"], {"a"}, 2) == 1.0
    assert 0.0 <= duplicate_card_rate(()) <= 1.0


def test_memory_retrieval_evaluation_reports_quality_and_budget_metrics() -> None:
    target = _memory("支付回调必须通过 Kafka")
    distractor = _memory("前端按钮规范")
    report = evaluate_memory_retrieval(
        (MemoryRetrievalCase("case-1", "支付回调 Kafka", (target, distractor), frozenset({str(target.id)})),),
        k=5,
    )
    assert report["case_count"] == 1
    assert report["recall_at_k"] == 1.0
    assert report["mrr"] == 1.0
    assert report["ndcg_at_k"] == 1.0
    assert report["average_card_chars"] > 0


def test_history_retrieval_evaluation_reports_project_scoped_recall() -> None:
    from project_lens.context.history_store import InMemoryHistoryMemoryStore
    from project_lens.domain.models import AgentRun

    project = ProjectRef(tenant_id="demo", project_id="payment")
    run = AgentRun(project=project, user_id="u", question="支付回调延迟")
    store = InMemoryHistoryMemoryStore()
    store.upsert_run(run, ())
    report = evaluate_history_retrieval(
        store,
        (HistoryRetrievalCase("history-1", project, "支付回调", frozenset({str(run.id)})),),
        k=5,
    )
    assert report["recall_at_k"] == 1.0
    assert report["mrr"] == 1.0
