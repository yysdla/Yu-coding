from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_store import InMemoryRiskStore, SQLiteRiskStore
from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.domain.risk import RiskEventType, RiskState, RiskType
from project_lens.persistence.sqlite import SQLiteDatabase


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
PROJECT = ProjectRef(tenant_id="tenant-1", project_id="project-1")


def item(
    source_id: str,
    *,
    kind: EvidenceType,
    metadata: dict[str, object],
    content: str | None = None,
    observed_at: datetime = NOW,
    scope: str = "project:read",
    system: str = "fixture",
) -> Evidence:
    body = content or f"evidence for {source_id}"
    return Evidence(
        type=kind,
        project=PROJECT,
        source=SourceRef(system=system, source_id=source_id),
        content=body,
        observed_at=observed_at,
        access_scope=scope,
        content_hash=sha256(body.encode()).hexdigest(),
        metadata=metadata,
    )


def test_engine_reproduces_all_six_risk_types_from_fixture_evidence() -> None:
    task_overdue = item(
        "TASK-1",
        kind=EvidenceType.TASK,
        metadata={
            "kind": "task",
            "task_id": "TASK-1",
            "status": "open",
            "due_at": (NOW - timedelta(days=2)).isoformat(),
        },
    )
    task_blocked = item(
        "TASK-2",
        kind=EvidenceType.TASK,
        metadata={
            "kind": "task",
            "task_id": "TASK-2",
            "status": "in_progress",
            "dependency_status": "waiting",
        },
    )
    task_requirement = item(
        "TASK-3",
        kind=EvidenceType.TASK,
        observed_at=NOW - timedelta(hours=2),
        metadata={"kind": "task", "task_id": "TASK-3", "status": "in_progress"},
    )
    requirement = item(
        "REQ-3",
        kind=EvidenceType.DOCUMENT,
        metadata={
            "kind": "requirement",
            "requirement_id": "REQ-3",
            "linked_task_ids": ["TASK-3"],
            "acceptance_criteria": ["defined"],
        },
    )
    task_code = item(
        "TASK-4",
        kind=EvidenceType.TASK,
        metadata={
            "kind": "task",
            "task_id": "TASK-4",
            "status": "done",
            "requires_code": True,
        },
    )
    pull_request = item(
        "PR-5",
        kind=EvidenceType.PULL_REQUEST,
        metadata={
            "pr_id": "PR-5",
            "ci_status": "failed",
            "task_status": "ready_to_release",
        },
    )
    chat = item(
        "MSG-6",
        kind=EvidenceType.DOCUMENT,
        content="明确阻塞：供应商接口尚未开放，无法继续。",
        metadata={"kind": "chat_message", "message_id": "MSG-6"},
        system="feishu_im",
    )

    result = RiskEngine(InMemoryRiskStore()).scan(
        PROJECT,
        (task_overdue, task_blocked, task_requirement, requirement, task_code, pull_request, chat),
        now=NOW,
    )

    assert {finding.risk_type for finding in result.open_findings} == set(RiskType)
    assert all(finding.evidence_ids for finding in result.findings)
    assert len(result.notify_risk_ids) == 6
    assert all(event.event_type == RiskEventType.DETECTED for event in result.events)


def test_context_engine_filters_acl_before_risk_rules() -> None:
    allowed = item(
        "TASK-ALLOWED",
        kind=EvidenceType.TASK,
        metadata={
            "kind": "task",
            "status": "open",
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    hidden = item(
        "TASK-HIDDEN",
        kind=EvidenceType.TASK,
        scope="project:secret",
        metadata={
            "kind": "task",
            "status": "open",
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    index = InMemoryEvidenceIndex()
    index.add_many((allowed, hidden))
    engine = ContextEngine(index)

    result = engine.scan_risks(
        PROJECT,
        AccessContext(
            tenant_id=PROJECT.tenant_id,
            user_id="u1",
            permissions=frozenset({"project:read"}),
        ),
        now=NOW,
    )

    assert len(result.open_findings) == 1
    assert result.open_findings[0].evidence_ids == (allowed.id,)


def test_limited_acl_scan_does_not_resolve_hidden_existing_risk() -> None:
    hidden = item(
        "TASK-HIDDEN",
        kind=EvidenceType.TASK,
        scope="project:secret",
        metadata={
            "kind": "task",
            "task_id": "TASK-HIDDEN",
            "status": "open",
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    index = InMemoryEvidenceIndex()
    index.add_many((hidden,))
    store = InMemoryRiskStore()
    engine = ContextEngine(index, risk_engine=RiskEngine(store))
    secret_access = AccessContext(
        tenant_id=PROJECT.tenant_id,
        user_id="admin",
        permissions=frozenset({"project:secret"}),
    )
    limited_access = AccessContext(
        tenant_id=PROJECT.tenant_id,
        user_id="member",
        permissions=frozenset({"project:read"}),
    )
    finding = engine.scan_risks(PROJECT, secret_access, now=NOW).open_findings[0]

    limited_result = engine.scan_risks(
        PROJECT,
        limited_access,
        now=NOW + timedelta(hours=1),
    )

    assert limited_result.events == ()
    assert store.get(finding.risk_id).state == RiskState.OPEN  # type: ignore[union-attr]


def test_unowned_risk_routes_to_project_owner_queue() -> None:
    overdue = item(
        "TASK-1",
        kind=EvidenceType.TASK,
        metadata={
            "kind": "task",
            "status": "open",
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    result = RiskEngine(InMemoryRiskStore()).scan(PROJECT, (overdue,), now=NOW)
    assert result.open_findings[0].owner_ids == ()
    assert result.open_findings[0].routing_queue == "project_owners"


def test_feedback_transition_is_persisted_with_actor_event() -> None:
    store = InMemoryRiskStore()
    engine = RiskEngine(store)
    overdue = item(
        "TASK-1",
        kind=EvidenceType.TASK,
        metadata={
            "kind": "task",
            "status": "open",
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    finding = engine.scan(PROJECT, (overdue,), now=NOW).open_findings[0]

    acknowledged = engine.transition(
        finding.risk_id,
        RiskState.ACKNOWLEDGED,
        actor_id="owner-1",
        now=NOW + timedelta(minutes=5),
    )

    assert acknowledged.state == RiskState.ACKNOWLEDGED
    event = store.events_for_risk(finding.risk_id)[-1]
    assert event.event_type == RiskEventType.STATE_CHANGED
    assert event.actor_id == "owner-1"


def test_sqlite_risk_store_survives_new_adapter_instance() -> None:
    database = SQLiteDatabase(":memory:")
    engine = RiskEngine(SQLiteRiskStore(database))
    overdue = item(
        "TASK-1",
        kind=EvidenceType.TASK,
        metadata={
            "kind": "task",
            "status": "open",
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    finding = engine.scan(PROJECT, (overdue,), now=NOW).open_findings[0]

    restored_store = SQLiteRiskStore(database)

    assert restored_store.get(finding.risk_id) == finding
    assert restored_store.events_for_risk(finding.risk_id)[0].event_type == RiskEventType.DETECTED
