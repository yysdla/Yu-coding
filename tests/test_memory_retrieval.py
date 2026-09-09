from datetime import datetime, timezone
from uuid import uuid4

from project_lens.context.memory_retrieval import (
    build_memory_summary,
    render_memory_summary,
    retrieve_memories,
)
from project_lens.domain.memory import MemoryType, ProjectMemory
from project_lens.domain.models import ProjectRef


def _memory(text: str, memory_type: MemoryType = MemoryType.ARCHITECTURE_FACT) -> ProjectMemory:
    return ProjectMemory(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        text=text,
        memory_type=memory_type,
        evidence_ids=(uuid4(),),
        approved_by="manager",
        valid_from=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )


def test_summary_is_navigation_metadata_not_memory_body() -> None:
    memories = (_memory("支付回调由 callback-service 接收并写入 Kafka"),)
    summary = build_memory_summary(memories)
    rendered = render_memory_summary(summary)

    assert "project_memory_directory:" in rendered
    assert "architecture_fact" in rendered
    assert "callback-service" in rendered
    assert "支付回调由 callback-service 接收并写入 Kafka" not in rendered


def test_retrieval_returns_bounded_cards_and_omitted_count() -> None:
    memories = tuple(_memory(f"Kafka payment callback retry rule {index}") for index in range(12))
    result = retrieve_memories("payment callback Kafka retry", memories, limit=99, budget_chars=900)

    assert result.returned_count <= 8
    assert result.returned_count < result.candidate_count
    assert result.omitted_count > 0
    assert all(len(card.snippet) <= 420 for card in result.cards)
    assert all("bm25" in card.retrieval_channels for card in result.cards)


def test_vector_scorer_is_optional_and_only_scores_authorized_candidates() -> None:
    memories = (_memory("消费者积压会导致回调延迟", MemoryType.RISK), _memory("前端按钮规范"))

    class Scorer:
        def score(self, query, candidates):
            assert tuple(candidates) == memories
            return {str(memories[0].id): 1.0}

    result = retrieve_memories("通知异常", memories, vector_scorer=Scorer())
    assert result.retrieval_mode == "hybrid"
    assert result.cards[0].memory_id == str(memories[0].id)
    assert "vector" in result.cards[0].retrieval_channels


def test_retrieval_reports_possible_conflicting_memories() -> None:
    memories = (
        _memory("支付回调必须通过 Kafka", MemoryType.RISK),
        _memory("支付回调禁止通过 Kafka", MemoryType.RISK),
    )
    result = retrieve_memories("支付回调 Kafka", memories, limit=8)
    assert result.conflicts
    assert str(memories[0].id) in result.conflicts[0]
