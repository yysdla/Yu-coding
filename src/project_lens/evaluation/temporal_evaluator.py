"""Deterministic temporal validity evaluation for retrieval results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from project_lens.evaluation.retrieval_cases import RetrievalCase
from project_lens.evaluation.retrieval_runner import RetrievalRun


@dataclass(frozen=True)
class TemporalEvaluation:
    metrics: dict[str, float | int]
    failures: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        return {"metrics": dict(self.metrics), "failures": list(self.failures)}


def evaluate_temporal_retrieval(
    cases: Iterable[RetrievalCase],
    run: RetrievalRun,
    *,
    k: int = 5,
) -> TemporalEvaluation:
    """Evaluate only cases with an explicit ``as_of`` timestamp.

    A temporal case is correct when a relevant result appears within ``k`` and
    no relevant result returned in that window is stale, expired, or revoked.
    This is intentionally a retrieval-level check; answer wording is evaluated
    separately.
    """

    case_list = tuple(cases)
    by_id = {str(item.get("id")): item for item in run.cases}
    temporal_cases = [case for case in case_list if case.as_of]
    correct = 0
    stale_relevant = 0
    failures: list[dict] = []

    for case in temporal_cases:
        result = by_id.get(case.id, {"retrieved": []})
        items = result.get("retrieved", [])[: max(1, k)]
        relevant = [item for item in items if item.get("relevant")]
        valid_relevant = [
            item
            for item in relevant
            if not item.get("stale")
            and not item.get("expired")
            and not item.get("revoked")
            and str(item.get("status", "")).casefold()
            not in {"revoked", "expired", "pending", "rejected"}
        ]
        bad_relevant = len(relevant) - len(valid_relevant)
        stale_relevant += bad_relevant
        case_ok = bool(valid_relevant) and bad_relevant == 0
        correct += int(case_ok)
        if not case_ok:
            failures.append(
                {
                    "id": case.id,
                    "relevant_count": len(relevant),
                    "valid_relevant_count": len(valid_relevant),
                    "reason": "no_valid_relevant_result_at_as_of",
                }
            )

    count = len(temporal_cases)
    relevant_total = sum(
        1
        for case in temporal_cases
        for item in by_id.get(case.id, {}).get("retrieved", [])[: max(1, k)]
        if item.get("relevant")
    )
    return TemporalEvaluation(
        metrics={
            "temporal_case_count": count,
            "temporal_correctness": correct / count if count else 1.0,
            "stale_relevant_rate": (
                stale_relevant / relevant_total
                if relevant_total
                else 0.0
            ),
        },
        failures=tuple(failures),
    )


__all__ = ["TemporalEvaluation", "evaluate_temporal_retrieval"]
