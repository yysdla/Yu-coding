"""Deterministic retrieval security and freshness checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from project_lens.evaluation.retrieval_cases import RetrievalCase


@dataclass(frozen=True)
class SecurityEvaluation:
    checks: dict[str, int]
    blockers: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict:
        return {
            "checks": dict(self.checks),
            "blockers": list(self.blockers),
            "passed": self.passed,
        }


def evaluate_retrieval_security(
    cases: Iterable[RetrievalCase],
    case_results: Iterable[dict],
) -> SecurityEvaluation:
    """Check returned objects without exposing hidden candidate payloads."""

    counts = {
        "cross_tenant_leakage": 0,
        "cross_project_leakage": 0,
        "forbidden_source_returned": 0,
        "revoked_result_used": 0,
        "expired_or_stale_result_used": 0,
        "pending_result_used": 0,
        "unauthorized_scorer_input": 0,
        "unauthorized_detail_read": 0,
        "snapshot_hash_mismatch_silently_accepted": 0,
    }
    for case, result in zip(tuple(cases), tuple(case_results), strict=True):
        forbidden = set(case.forbidden_object_ids)
        for item in result.get("retrieved", []):
            object_id = str(item.get("object_id", ""))
            if str(item.get("tenant_id")) != case.tenant_id:
                counts["cross_tenant_leakage"] += 1
            if str(item.get("project_id")) != case.project_id:
                counts["cross_project_leakage"] += 1
            if object_id in forbidden:
                counts["forbidden_source_returned"] += 1
            if item.get("revoked"):
                counts["revoked_result_used"] += 1
            if item.get("expired"):
                counts["expired_or_stale_result_used"] += 1
            if str(item.get("status", "")).casefold() in {"pending", "rejected"}:
                counts["pending_result_used"] += 1
        trace = result.get("retrieval_trace", {})
        vector_candidates = trace.get("vector_candidate_count")
        authorized_candidates = trace.get("authorized_candidate_count")
        if (
            vector_candidates is not None
            and authorized_candidates is not None
            and int(vector_candidates) > int(authorized_candidates)
        ):
            counts["unauthorized_scorer_input"] += 1
        counts["unauthorized_detail_read"] += int(bool(trace.get("unauthorized_detail_read")))
        counts["snapshot_hash_mismatch_silently_accepted"] += int(
            bool(trace.get("snapshot_hash_mismatch_silently_accepted"))
        )

    blockers = tuple(name for name, value in counts.items() if value)
    return SecurityEvaluation(checks=counts, blockers=blockers)


__all__ = ["SecurityEvaluation", "evaluate_retrieval_security"]
