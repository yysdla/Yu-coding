from project_lens.application.release_gates import evaluate_release_gates

def test_release_gate_blocks_without_required_measurements() -> None:
    report = evaluate_release_gates(mode="pilot", service_auth_configured=True, readiness_ok=True)
    assert report.ready is False
    assert "replay_evaluation" in report.blockers

def test_production_release_gate_blocks_without_memory_measurements() -> None:
    report = evaluate_release_gates(mode="production", service_auth_configured=True, readiness_ok=True, citation_coverage=1.0, unauthorized_writes=0, leakage_count=0, success_rate=0.99, fallback_rate=0.01, duplicate_notification_rate=0.0, replay_sample_count=100, pilot_days=14)
    assert report.ready is False
    assert "snapshot_mismatch_zero" in report.blockers


def test_release_gate_passes_only_after_all_thresholds() -> None:
    report = evaluate_release_gates(mode="production", service_auth_configured=True, readiness_ok=True, citation_coverage=1.0, unauthorized_writes=0, leakage_count=0, success_rate=0.99, fallback_rate=0.01, duplicate_notification_rate=0.0, replay_sample_count=100, pilot_days=14, revoked_memory_usage=0, expired_memory_usage=0, invalid_provenance=0, snapshot_mismatch=0, temporal_accuracy=0.95, memory_recall_at_5=0.8, citation_correctness=0.95)
    assert report.ready is True
