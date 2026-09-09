from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_store import InMemoryRiskStore
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.domain.risk import RiskEventType, RiskState, stable_risk_id


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
PROJECT = ProjectRef(tenant_id="tenant-1", project_id="project-1")


def task(*, status: str = "open", due_days_ago: int = 2) -> Evidence:
    content = f"TASK-1 status={status} due_days_ago={due_days_ago}"
    return Evidence(
        type=EvidenceType.TASK,
        project=PROJECT,
        source=SourceRef(system="fixture", source_id="TASK-1"),
        content=content,
        observed_at=NOW,
        access_scope="project:read",
        content_hash=sha256(content.encode()).hexdigest(),
        metadata={
            "kind": "task",
            "task_id": "TASK-1",
            "status": status,
            "due_at": (NOW - timedelta(days=due_days_ago)).isoformat(),
            "owner_ids": ["owner-1"],
        },
    )


def test_same_evidence_refreshes_last_seen_without_duplicate_event_or_notification() -> None:
    store = InMemoryRiskStore()
    engine = RiskEngine(store)
    overdue = task()
    first = engine.scan(PROJECT, (overdue,), now=NOW)

    second = engine.scan(PROJECT, (overdue,), now=NOW + timedelta(hours=1))

    assert second.findings[0].risk_id == first.findings[0].risk_id
    assert second.findings[0].last_seen_at == NOW + timedelta(hours=1)
    assert second.events == ()
    assert second.notify_risk_ids == ()
    assert len(store.events_for_risk(first.findings[0].risk_id)) == 1


def test_completed_task_automatically_resolves_previous_risk() -> None:
    store = InMemoryRiskStore()
    engine = RiskEngine(store)
    initial = task()
    finding = engine.scan(PROJECT, (initial,), now=NOW).open_findings[0]
    completed = task(status="done")

    result = engine.scan(PROJECT, (completed,), now=NOW + timedelta(hours=1))

    resolved = next(item for item in result.findings if item.risk_id == finding.risk_id)
    assert resolved.state == RiskState.RESOLVED
    assert resolved.resolved_at == NOW + timedelta(hours=1)
    assert result.events[0].event_type == RiskEventType.RESOLVED
    assert result.notify_risk_ids == ()


def test_severity_upgrade_creates_event_and_notification() -> None:
    store = InMemoryRiskStore()
    engine = RiskEngine(store)
    overdue = task(due_days_ago=2)
    finding = engine.scan(PROJECT, (overdue,), now=NOW).open_findings[0]

    result = engine.scan(PROJECT, (overdue,), now=NOW + timedelta(days=6))

    assert result.findings[0].risk_id == finding.risk_id
    assert result.findings[0].severity.value == "high"
    assert result.events[0].event_type == RiskEventType.SEVERITY_CHANGED
    assert result.notify_risk_ids == (finding.risk_id,)


def test_stable_fingerprint_canonicalizes_owner_order() -> None:
    from project_lens.domain.risk import RiskType

    first = stable_risk_id(PROJECT, RiskType.OVERDUE_TASK, "TASK-1", ["b", "a"])
    second = stable_risk_id(PROJECT, RiskType.OVERDUE_TASK, "TASK-1", ["a", "b", "a"])
    assert first == second


def test_reappearing_resolved_risk_reopens_and_notifies() -> None:
    store = InMemoryRiskStore()
    engine = RiskEngine(store)
    overdue = task()
    finding = engine.scan(PROJECT, (overdue,), now=NOW).open_findings[0]
    engine.scan(PROJECT, (task(status="done"),), now=NOW + timedelta(hours=1))

    result = engine.scan(PROJECT, (overdue,), now=NOW + timedelta(hours=2))

    reopened = next(item for item in result.findings if item.risk_id == finding.risk_id)
    assert reopened.state == RiskState.OPEN
    assert result.events[0].event_type == RiskEventType.STATE_CHANGED
    assert result.notify_risk_ids == (finding.risk_id,)
