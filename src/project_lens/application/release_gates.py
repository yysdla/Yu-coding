"""Deterministic production/pilot release gate evaluation."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class ReleaseGateReport:
    ready: bool
    mode: str
    checks: dict[str, bool]
    blockers: tuple[str, ...]

def evaluate_release_gates(*, mode: str, service_auth_configured: bool, readiness_ok: bool, citation_coverage: float | None = None, unauthorized_writes: int | None = None, leakage_count: int | None = None, success_rate: float | None = None, fallback_rate: float | None = None, duplicate_notification_rate: float | None = None, replay_sample_count: int = 0, pilot_days: int = 0, revoked_memory_usage: int | None = None, expired_memory_usage: int | None = None, invalid_provenance: int | None = None, snapshot_mismatch: int | None = None, temporal_accuracy: float | None = None, memory_recall_at_5: float | None = None, citation_correctness: float | None = None) -> ReleaseGateReport:
    checks = {"service_auth": service_auth_configured, "readiness": readiness_ok, "citation_coverage": citation_coverage is not None and citation_coverage >= 1.0, "unauthorized_writes_zero": unauthorized_writes == 0, "leakage_zero": leakage_count == 0, "success_rate": success_rate is not None and success_rate >= 0.98, "fallback_rate": fallback_rate is not None and fallback_rate < 0.05, "duplicate_notification_rate": duplicate_notification_rate is not None and duplicate_notification_rate < 0.01, "replay_evaluation": replay_sample_count >= 100, "pilot_duration": pilot_days >= 14}
    if mode == "production":
        checks.update({
            "revoked_memory_usage_zero": revoked_memory_usage == 0,
            "expired_memory_usage_zero": expired_memory_usage == 0,
            "invalid_provenance_zero": invalid_provenance == 0,
            "snapshot_mismatch_zero": snapshot_mismatch == 0,
            "temporal_accuracy": temporal_accuracy is not None and temporal_accuracy >= 0.95,
            "memory_recall_at_5": memory_recall_at_5 is not None and memory_recall_at_5 >= 0.80,
            "citation_correctness": citation_correctness is not None and citation_correctness >= 0.95,
        })
    blockers = tuple(name for name, passed in checks.items() if not passed)
    return ReleaseGateReport(ready=not blockers and mode in {"pilot", "production"}, mode=mode, checks=checks, blockers=blockers)
