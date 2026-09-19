from pathlib import Path

import pytest

from project_lens.application.release_gates import evaluate_release_gates
from project_lens.evaluation.memory_cases import load_memory_cases, validate_memory_security_metrics


ROOT = Path(__file__).parents[1]


def test_memory_case_sets_have_required_shape_and_unique_ids() -> None:
    cases = load_memory_cases(ROOT / "evaluation" / "memory_cases.json")
    assert len(cases) >= 10
    assert load_memory_cases(ROOT / "evaluation" / "memory_security_cases.json")
    assert load_memory_cases(ROOT / "evaluation" / "memory_temporal_cases.json")


def test_security_metrics_are_hard_blockers() -> None:
    assert validate_memory_security_metrics({}) == ()
    assert "snapshot_mismatch" in validate_memory_security_metrics({"snapshot_mismatch": 1})


def test_production_gate_requires_memory_quality_metrics() -> None:
    report = evaluate_release_gates(
        mode="production", service_auth_configured=True, readiness_ok=True,
        citation_coverage=1.0, unauthorized_writes=0, leakage_count=0,
        success_rate=0.99, fallback_rate=0.01, duplicate_notification_rate=0.0,
        replay_sample_count=100, pilot_days=14,
        revoked_memory_usage=0, expired_memory_usage=0, invalid_provenance=0,
        snapshot_mismatch=0, temporal_accuracy=0.96, memory_recall_at_5=0.81,
        citation_correctness=0.96,
    )
    assert report.ready is True


def test_malformed_memory_case_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("[{\"id\": \"missing-fields\"}]", encoding="utf-8")
    with pytest.raises(ValueError, match="missing fields"):
        load_memory_cases(path)
