"""Offline metrics for ProjectLens long-term memory retrieval."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from project_lens.context.memory_retrieval import MemoryCard, retrieve_memories
from project_lens.domain.memory import Episode, ProjectMemory
from project_lens.domain.models import ProjectRef


@dataclass(frozen=True)
class MemoryRetrievalCase:
    """One replay query with authorized candidates and expected memory IDs."""

    case_id: str
    query: str
    candidates: tuple[ProjectMemory, ...]
    relevant_ids: frozenset[str]


@dataclass(frozen=True)
class HistoryRetrievalCase:
    """One replay query with expected historical Episode/run IDs."""

    case_id: str
    project: ProjectRef
    query: str
    relevant_run_ids: frozenset[str]


def dcg(relevances: Sequence[float]) -> float:
    return sum(float(value) / math.log2(index + 2) for index, value in enumerate(relevances))


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    actual = dcg([1.0 if item in relevant else 0.0 for item in retrieved[: max(1, int(k))]])
    ideal = dcg([1.0] * min(len(relevant), max(1, int(k))))
    return actual / ideal if ideal else 0.0


def duplicate_card_rate(cards: Sequence[MemoryCard]) -> float:
    if not cards:
        return 0.0
    unique = {"".join(card.snippet.casefold().split()) for card in cards}
    return 1.0 - (len(unique) / len(cards))


def average_card_chars(cards_by_case: Sequence[Sequence[MemoryCard]]) -> float:
    cards = [card for case in cards_by_case for card in case]
    return sum(len(card.snippet) + 180 for card in cards) / len(cards) if cards else 0.0


def evaluate_memory_retrieval(
    cases: Sequence[MemoryRetrievalCase],
    *,
    k: int = 5,
    vector_scorer=None,
) -> dict[str, object]:
    """Run bounded retrieval and return aggregate plus per-case metrics."""

    bounded_k = max(1, int(k))
    per_case: list[dict[str, object]] = []
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    all_cards: list[Sequence[MemoryCard]] = []
    conflict_count = 0
    for case in cases:
        result = retrieve_memories(case.query, case.candidates, limit=bounded_k, vector_scorer=vector_scorer)
        ids = [card.memory_id for card in result.cards]
        relevant = set(case.relevant_ids)
        hit_ids = set(ids[:bounded_k]) & relevant
        recall = len(hit_ids) / len(relevant) if relevant else 0.0
        reciprocal_rank = next((1.0 / index for index, item in enumerate(ids, start=1) if item in relevant), 0.0)
        ndcg = ndcg_at_k(ids, relevant, bounded_k)
        recalls.append(recall)
        reciprocal_ranks.append(reciprocal_rank)
        ndcgs.append(ndcg)
        all_cards.append(result.cards)
        conflict_count += len(result.conflicts)
        per_case.append({
            "id": case.case_id,
            "retrieved": ids,
            "relevant": sorted(relevant),
            "recall_at_k": recall,
            "mrr": reciprocal_rank,
            "ndcg_at_k": ndcg,
            "duplicate_rate": duplicate_card_rate(result.cards),
            "retrieval_mode": result.retrieval_mode,
            "conflict_count": len(result.conflicts),
        })
    return {
        "case_count": len(cases),
        "k": bounded_k,
        "recall_at_k": sum(recalls) / len(recalls) if recalls else 0.0,
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "ndcg_at_k": sum(ndcgs) / len(ndcgs) if ndcgs else 0.0,
        "duplicate_card_rate": sum(duplicate_card_rate(cards) for cards in all_cards) / len(all_cards) if all_cards else 0.0,
        "average_card_chars": average_card_chars(all_cards),
        "conflict_count": conflict_count,
        "cases": per_case,
    }


def evaluate_history_retrieval(
    store,
    cases: Sequence[HistoryRetrievalCase],
    *,
    k: int = 5,
) -> dict[str, object]:
    """Evaluate project-scoped Episode search without accessing raw events."""

    bounded_k = max(1, int(k))
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    per_case: list[dict[str, object]] = []
    for case in cases:
        episodes: tuple[Episode, ...] = tuple(
            store.search_episodes(case.project, case.query, limit=bounded_k)
        )
        ids = [str(item.run_id) for item in episodes[:bounded_k]]
        relevant = set(case.relevant_run_ids)
        recalls.append(len(set(ids) & relevant) / len(relevant) if relevant else 0.0)
        reciprocal_rank = next(
            (1.0 / index for index, item in enumerate(ids, start=1) if item in relevant),
            0.0,
        )
        reciprocal_ranks.append(reciprocal_rank)
        per_case.append(
            {
                "id": case.case_id,
                "retrieved": ids,
                "relevant": sorted(relevant),
                "recall_at_k": recalls[-1],
                "mrr": reciprocal_rank,
            }
        )
    return {
        "case_count": len(cases),
        "k": bounded_k,
        "recall_at_k": sum(recalls) / len(recalls) if recalls else 0.0,
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "cases": per_case,
    }
