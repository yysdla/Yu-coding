"""Deterministic validation and release checks for memory evaluation cases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_CASE_FIELDS = {
    "id",
    "project",
    "query",
    "as_of",
    "relevant_memory_ids",
    "forbidden_memory_ids",
    "expected_abstention",
    "expected_conflict",
    "expected_evidence_ids",
}


def load_memory_cases(path: str | Path) -> tuple[dict[str, Any], ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("memory evaluation data must be a JSON array")
    cases = tuple(_validate_case(item, index=index) for index, item in enumerate(payload))
    ids = [item["id"] for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("memory evaluation case ids must be unique")
    return cases


def validate_memory_security_metrics(metrics: dict[str, int]) -> tuple[str, ...]:
    """Return deterministic blockers for security-sensitive memory evaluation."""

    checks = {
        "cross_scope_leakage": metrics.get("cross_scope_leakage", 0) == 0,
        "revoked_memory_usage": metrics.get("revoked_memory_usage", 0) == 0,
        "expired_memory_usage": metrics.get("expired_memory_usage", 0) == 0,
        "invalid_provenance": metrics.get("invalid_provenance", 0) == 0,
        "snapshot_mismatch": metrics.get("snapshot_mismatch", 0) == 0,
    }
    return tuple(name for name, passed in checks.items() if not passed)


def _validate_case(item: object, *, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"memory evaluation case {index} must be an object")
    missing = REQUIRED_CASE_FIELDS - set(item)
    if missing:
        raise ValueError(f"memory evaluation case {index} missing fields: {sorted(missing)}")
    if not isinstance(item["id"], str) or not item["id"].strip():
        raise ValueError(f"memory evaluation case {index} id must be non-empty")
    if not isinstance(item["project"], dict) or not item["project"].get("tenant_id") or not item["project"].get("project_id"):
        raise ValueError(f"memory evaluation case {index} project must include tenant_id/project_id")
    for key in ("relevant_memory_ids", "forbidden_memory_ids", "expected_evidence_ids"):
        if not isinstance(item[key], list) or any(not isinstance(value, str) for value in item[key]):
            raise ValueError(f"memory evaluation case {index} {key} must be a string array")
    for key in ("expected_abstention", "expected_conflict"):
        if not isinstance(item[key], bool):
            raise ValueError(f"memory evaluation case {index} {key} must be boolean")
    return dict(item)
