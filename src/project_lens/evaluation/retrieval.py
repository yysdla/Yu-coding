"""Small retrieval metrics used by the local incident replay benchmark."""

from __future__ import annotations

from collections.abc import Sequence
import math


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(cases: Sequence[tuple[Sequence[str], set[str]]]) -> float:
    if not cases:
        return 0.0
    return sum(reciprocal_rank(retrieved, relevant) for retrieved, relevant in cases) / len(cases)


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Binary-relevance nDCG for a single ranked result list."""

    if not relevant or k <= 0:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(retrieved[:k], start=1)
        if item in relevant
    )
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal_dcg if ideal_dcg else 0.0
