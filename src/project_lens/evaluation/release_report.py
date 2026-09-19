"""Reproducible retrieval evaluation reports and release gates."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_lens.evaluation.retrieval_cases import dataset_fingerprint
from project_lens.evaluation.retrieval_runner import RetrievalRun, compare_vector_modes
from project_lens.evaluation.security_evaluator import SecurityEvaluation


def build_retrieval_report(
    *,
    project_root: Path,
    dataset_path: Path,
    runs: dict[str, RetrievalRun],
    security: SecurityEvaluation,
    retrieval_config: dict[str, Any] | None = None,
    index_snapshot: str | None = None,
    chunker_version: str | None = None,
    embedding_model_version: str | None = None,
    dataset_version: str | None = None,
    gates_path: Path | None = None,
    answer_metrics: dict[str, float | int] | None = None,
    temporal_metrics: dict[str, float | int] | None = None,
    answer_failures: list[dict] | None = None,
    temporal_failures: list[dict] | None = None,
) -> dict[str, Any]:
    gate_config = _load_gate_config(gates_path)
    case_count = max((int(run.metrics.get("case_count", 0)) for run in runs.values()), default=0)
    metrics = {mode: dict(run.metrics) for mode, run in runs.items()}
    metrics["comparison"] = compare_vector_modes(runs, k=int(gate_config["comparison_k"]))
    decision, blockers = _decide_gate(
        case_count=case_count,
        runs=runs,
        security=security,
        gate_config=gate_config,
        answer_metrics=answer_metrics,
        temporal_metrics=temporal_metrics,
    )
    return {
        "schema_version": "retrieval-evaluation-report.v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": _git_revision(project_root),
        "dataset": {
            "path": str(dataset_path),
            "version": dataset_version or dataset_fingerprint(dataset_path),
            "sha256": dataset_fingerprint(dataset_path),
            "case_count": case_count,
        },
        "index_snapshot": index_snapshot,
        "chunker_version": chunker_version,
        "embedding_model_version": embedding_model_version,
        "retrieval_config": retrieval_config or {},
        "metrics": metrics,
        "runs": {mode: run.as_dict() for mode, run in runs.items()},
        "security": security.as_dict(),
        "answer_metrics": answer_metrics,
        "temporal_metrics": temporal_metrics,
        "answer_failures": answer_failures or [],
        "temporal_failures": temporal_failures or [],
        "gate": {
            "decision": decision,
            "blockers": blockers,
            "config": gate_config,
        },
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _decide_gate(
    *,
    case_count: int,
    runs: dict[str, RetrievalRun],
    security: SecurityEvaluation,
    gate_config: dict[str, Any],
    answer_metrics: dict[str, float | int] | None,
    temporal_metrics: dict[str, float | int] | None,
) -> tuple[str, list[str]]:
    if security.blockers:
        return "fail", list(security.blockers)
    if case_count < int(gate_config["minimum_cases"]):
        return "insufficient_data", [
            f"golden_cases<{int(gate_config['minimum_cases'])}"
        ]
    lexical = runs.get("lexical")
    hybrid = runs.get("hybrid")
    if lexical is None or hybrid is None:
        return "not_run", ["lexical_and_hybrid_runs_required"]
    blockers: list[str] = []
    for metric in ("recall_at_5", "mrr"):
        if float(hybrid.metrics.get(metric, 0.0) or 0.0) < float(
            lexical.metrics.get(metric, 0.0) or 0.0
        ):
            blockers.append(f"hybrid_{metric}_below_lexical")
    fallback_rate = hybrid.metrics.get("fallback_success_rate")
    if fallback_rate is None:
        blockers.append("fallback_measurement_required")
    elif float(fallback_rate) < float(
        gate_config.get("thresholds", {}).get("fallback_success_rate", 1.0)
    ):
        blockers.append("fallback_success_rate_below_threshold")
    if answer_metrics is None:
        blockers.append("answer_evaluation_required")
    else:
        for metric, threshold in (
            ("citation_correctness", gate_config.get("thresholds", {}).get("citation_correctness", 0.95)),
            ("conflict_recall", gate_config.get("thresholds", {}).get("conflict_recall", 0.95)),
            ("abstention_precision", gate_config.get("thresholds", {}).get("abstention_precision", 0.95)),
        ):
            if metric not in answer_metrics:
                blockers.append(f"{metric}_measurement_required")
            elif float(answer_metrics[metric]) < threshold:
                blockers.append(f"{metric}_below_threshold")
        if int(answer_metrics.get("conflict_case_count", 0)) <= 0:
            blockers.append("conflict_cases_required")
        if int(answer_metrics.get("abstention_case_count", 0)) <= 0:
            blockers.append("abstention_cases_required")
    if temporal_metrics is None:
        blockers.append("temporal_evaluation_required")
    elif int(temporal_metrics.get("temporal_case_count", 0)) <= 0:
        blockers.append("temporal_cases_required")
    elif float(temporal_metrics.get("temporal_correctness", 0.0)) < float(
        gate_config.get("thresholds", {}).get("temporal_correctness", 0.95)
    ):
        blockers.append("temporal_correctness_below_threshold")
    return ("fail", blockers) if blockers else ("pass", [])


def _load_gate_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "minimum_cases": 100,
            "comparison_k": 5,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "minimum_cases": int(payload.get("minimum_cases", 100)),
        "comparison_k": int(payload.get("comparison_k", 5)),
        **payload,
    }


def _git_revision(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    revision = result.stdout.strip()
    return revision or None


__all__ = ["build_retrieval_report", "write_report"]
