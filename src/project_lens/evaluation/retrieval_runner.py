"""Deterministic multi-mode retrieval evaluation runner."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle
from project_lens.domain.models import ProjectRef
from project_lens.evaluation.retrieval import reciprocal_rank
from project_lens.evaluation.retrieval_cases import RetrievalCase


@dataclass(frozen=True)
class RetrievalRun:
    mode: str
    metrics: dict[str, float | int | None]
    cases: tuple[dict, ...]
    degraded_reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "metrics": dict(self.metrics),
            "cases": list(self.cases),
            "degraded_reasons": list(self.degraded_reasons),
        }


def run_retrieval_modes(
    cases: Iterable[RetrievalCase],
    *,
    engine_factory: Callable[[str], ContextEngine],
    modes: tuple[str, ...] = ("lexical", "vector", "hybrid"),
    limit: int = 10,
    access_factory: Callable[[RetrievalCase], AccessContext] | None = None,
) -> dict[str, RetrievalRun]:
    """Replay the same cases against independently configured engines."""

    case_list = tuple(cases)
    return {
        mode: run_retrieval(
            case_list,
            engine=engine_factory(mode),
            mode=mode,
            limit=limit,
            access_factory=access_factory,
        )
        for mode in modes
    }


def run_retrieval(
    cases: Iterable[RetrievalCase],
    *,
    engine: ContextEngine,
    mode: str,
    limit: int = 10,
    access_factory: Callable[[RetrievalCase], AccessContext] | None = None,
) -> RetrievalRun:
    case_list = tuple(cases)
    per_case: list[dict] = []
    degraded: list[str] = []
    latencies: list[float] = []

    for case in case_list:
        project = ProjectRef(tenant_id=case.tenant_id, project_id=case.project_id)
        access = access_factory(case) if access_factory else _default_access(case)
        started = time.perf_counter()
        bundle = engine.search(
            ContextQuery(
                text=case.query,
                project=project,
                limit=max(1, min(limit, 50)),
                retrieval_mode=mode,
            ),
            access,
        )
        latency_ms = (time.perf_counter() - started) * 1_000
        latencies.append(latency_ms)
        result = _case_result(case, bundle, latency_ms=latency_ms)
        per_case.append(result)
        reason = bundle.retrieval_trace.get("fallback_reason")
        if reason:
            degraded.append(str(reason))

    metrics = _aggregate_metrics(case_list, per_case, latencies)
    return RetrievalRun(
        mode=mode,
        metrics=metrics,
        cases=tuple(per_case),
        degraded_reasons=tuple(sorted(set(degraded))),
    )


def _case_result(case: RetrievalCase, bundle: EvidenceBundle, *, latency_ms: float) -> dict:
    relevant_objects = set(case.relevant_object_ids) | set(case.expected_evidence_ids)
    relevant_sources = set(case.relevant_source_keys)
    as_of = _parse_datetime(case.as_of)
    retrieved: list[dict] = []
    for rank, hit in enumerate(bundle.hits, start=1):
        evidence = hit.evidence
        source_key = f"{evidence.source.system}:{evidence.source.source_id}"
        source_id = evidence.source.source_id
        relevant = (
            str(evidence.id) in relevant_objects
            or source_key in relevant_sources
            or source_id in relevant_sources
        )
        valid_to = _parse_datetime(evidence.metadata.get("valid_to"))
        stale = bool(as_of and evidence.observed_at > as_of)
        expired = bool(as_of and valid_to and valid_to <= as_of)
        retrieved.append(
            {
                "rank": rank,
                "object_id": str(evidence.id),
                "source_key": source_key,
                "source_id": source_id,
                "project_id": evidence.project.project_id,
                "tenant_id": evidence.project.tenant_id,
                "revoked": evidence.revoked,
                "status": str(evidence.metadata.get("status") or ""),
                "observed_at": evidence.observed_at.isoformat(),
                "stale": stale,
                "expired": expired,
                "relevant": relevant,
                "channels": list(hit.channels),
                "channel_ranks": dict(hit.channel_ranks),
                "channel_scores": dict(hit.channel_scores),
                "score": hit.score,
            }
        )
    return {
        "id": case.id,
        "retrieved": retrieved,
        "retrieval_trace": dict(bundle.retrieval_trace),
        "warnings": list(bundle.warnings),
        "latency_ms": round(latency_ms, 4),
        "expected_abstention": case.expected_abstention,
        "expected_conflict": case.expected_conflict,
        "forbidden_object_ids": list(case.forbidden_object_ids),
    }


def _aggregate_metrics(
    cases: tuple[RetrievalCase, ...],
    results: list[dict],
    latencies: list[float],
) -> dict[str, float | int | None]:
    if not cases:
        return {"case_count": 0}

    metric_values: dict[str, list[float]] = {
        "recall_at_1": [],
        "recall_at_5": [],
        "recall_at_10": [],
        "hit_rate_at_1": [],
        "hit_rate_at_5": [],
        "hit_rate_at_10": [],
        "mrr": [],
        "ndcg_at_5": [],
    }
    returned = 0
    irrelevant = 0
    duplicate_count = 0
    source_diversity: list[float] = []
    stale = 0
    abstention_correct = 0

    for case, result in zip(cases, results, strict=True):
        items = result["retrieved"]
        relevant_count = len(
            set(case.relevant_object_ids)
            | set(case.expected_evidence_ids)
            | set(case.relevant_source_keys)
        )
        relevant_flags = [bool(item["relevant"]) for item in items]
        for k in (1, 5, 10):
            hits = sum(relevant_flags[:k])
            metric_values[f"hit_rate_at_{k}"].append(float(hits > 0))
            metric_values[f"recall_at_{k}"].append(
                hits / relevant_count if relevant_count else float(not hits)
            )
        metric_values["mrr"].append(
            reciprocal_rank(
                [str(item["object_id"]) if item["relevant"] else "" for item in items],
                {str(item["object_id"]) for item in items if item["relevant"]},
            )
        )
        metric_values["ndcg_at_5"].append(
            _binary_ndcg(relevant_flags, relevant_count, 5)
        )
        returned += len(items)
        irrelevant += sum(not item["relevant"] for item in items)
        sources = [str(item["source_id"]) for item in items]
        duplicate_count += len(sources) - len(set(sources))
        source_diversity.append(len(set(sources)) / len(sources) if sources else 1.0)
        stale += sum(bool(item["stale"] or item["expired"]) for item in items)
        if case.expected_abstention:
            abstention_correct += int(not items)

    metrics: dict[str, float | int | None] = {
        "case_count": len(cases),
        **{name: _mean(values) for name, values in metric_values.items()},
        "irrelevant_rate": irrelevant / returned if returned else 0.0,
        "duplicate_chunk_rate": duplicate_count / returned if returned else 0.0,
        "source_diversity": _mean(source_diversity),
        "stale_result_rate": stale / returned if returned else 0.0,
        "abstention_accuracy": (
            abstention_correct / sum(case.expected_abstention for case in cases)
            if any(case.expected_abstention for case in cases)
            else None
        ),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "embedding_cache_hit_rate": _trace_rate(results, "embedding_cache_hit_rate"),
        "fallback_success_rate": _fallback_success_rate(results),
        "returned_count": returned,
    }
    return metrics


def compare_vector_modes(
    runs: dict[str, RetrievalRun],
    *,
    k: int = 5,
) -> dict[str, float | None]:
    lexical = runs.get("lexical")
    hybrid = runs.get("hybrid")
    vector = runs.get("vector")
    if lexical is None or hybrid is None:
        return {"vector_rescue_rate": None, "vector_false_positive_rate": None}

    rescued = 0
    eligible = 0
    for lexical_case, hybrid_case in zip(lexical.cases, hybrid.cases, strict=True):
        lexical_hit = any(item["relevant"] for item in lexical_case["retrieved"][:k])
        hybrid_hit = any(item["relevant"] for item in hybrid_case["retrieved"][:k])
        eligible += 1
        rescued += int(not lexical_hit and hybrid_hit)

    false_positive_rate = None
    if vector is not None:
        vector_items = [
            item
            for case in vector.cases
            for item in case["retrieved"][:k]
        ]
        false_positive_rate = (
            sum(not item["relevant"] for item in vector_items) / len(vector_items)
            if vector_items
            else 0.0
        )
    return {
        "vector_rescue_rate": rescued / eligible if eligible else 0.0,
        "vector_false_positive_rate": false_positive_rate,
    }


def _default_access(case: RetrievalCase) -> AccessContext:
    return AccessContext(
        tenant_id=case.tenant_id,
        user_id="evaluation-runner",
        permissions=frozenset({f"project:{case.project_id}:read"}),
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return round(ordered[index], 4)


def _trace_rate(results: list[dict], key: str) -> float | None:
    values = [
        float(result["retrieval_trace"][key])
        for result in results
        if key in result["retrieval_trace"]
        and result["retrieval_trace"][key] is not None
    ]
    return _mean(values) if values else None


def _fallback_success_rate(results: list[dict]) -> float | None:
    degraded = [
        result
        for result in results
        if result["retrieval_trace"].get("fallback_reason")
    ]
    if not degraded:
        return None
    return _mean([float(bool(result["retrieved"])) for result in degraded])


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _binary_ndcg(flags: list[bool], relevant_count: int, k: int) -> float:
    if relevant_count <= 0 or k <= 0:
        return 0.0
    import math

    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, relevant in enumerate(flags[:k], start=1)
        if relevant
    )
    ideal_hits = min(relevant_count, k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return round(dcg / ideal, 6) if ideal else 0.0


__all__ = [
    "RetrievalRun",
    "compare_vector_modes",
    "run_retrieval",
    "run_retrieval_modes",
]
