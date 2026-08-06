from datetime import datetime, timedelta, timezone
from pathlib import Path

from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext, ContextQuery, TimeRange
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from tests.context_helpers import ACCESS_SCOPE, build_demo_engine


DEMO_ROOT = Path(__file__).parents[1] / "examples" / "payment_service"


def access(*permissions: str) -> AccessContext:
    return AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset(permissions),
    )


def test_traceback_exactly_retrieves_source_function() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "src/order_service.py", line 14, in create_order\n'
        "    coupon_id = request.coupon.id\n"
        "AttributeError: 'NoneType' object has no attribute 'id'"
    )

    bundle = engine.search(
        ContextQuery(text=traceback, project=project, limit=5),
        access(ACCESS_SCOPE),
    )

    assert bundle.hits
    first = bundle.hits[0]
    assert first.evidence.metadata.get("symbol") == "create_order"
    assert "exact" in first.channels
    assert bundle.retrieval_trace["traceback_frames"] == 1


def test_semantic_keyword_query_retrieves_incident_and_runbook() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)

    bundle = engine.search(
        ContextQuery(
            text="orders without coupons missing null guard regression test",
            project=project,
            limit=5,
        ),
        access(ACCESS_SCOPE),
    )

    result_types = {hit.evidence.type for hit in bundle.hits}
    assert EvidenceType.INCIDENT in result_types
    assert EvidenceType.DOCUMENT in result_types


def test_access_filter_runs_before_retrieval() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    restricted = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="secret", source_id="secret-doc"),
        content="unique-secret-root-cause",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:secret",
        content_hash="1234567890abcdef",
    )
    index = InMemoryEvidenceIndex()
    index.add_many([restricted])

    bundle = ContextEngine(index).search(
        ContextQuery(text="unique-secret-root-cause", project=project),
        access(ACCESS_SCOPE),
    )

    assert not bundle.hits
    assert bundle.retrieval_trace["authorized_candidate_count"] == 0


def test_service_filter_excludes_other_service() -> None:
    engine, index, project = build_demo_engine(DEMO_ROOT)
    other_project = project.model_copy(update={"service": "catalog-service"})
    other = Evidence(
        type=EvidenceType.DOCUMENT,
        project=other_project,
        source=SourceRef(system="local_document", source_id="catalog.md#chunk-0"),
        content="coupon null guard catalog only",
        observed_at=datetime.now(timezone.utc),
        access_scope=ACCESS_SCOPE,
        content_hash="abcdef1234567890",
    )
    index.add_many([other])

    bundle = engine.search(
        ContextQuery(text="coupon null guard", project=project),
        access(ACCESS_SCOPE),
    )

    assert all(hit.evidence.project.service != "catalog-service" for hit in bundle.hits)


def test_project_snapshot_is_built_from_authorized_project_evidence() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)

    snapshot = engine.snapshot(project, access(ACCESS_SCOPE))

    assert "order-service" in snapshot.services
    assert "create_order" in snapshot.entrypoints
    assert "payment service" in snapshot.dependencies
    assert "optional coupon handling" in snapshot.risks
    assert snapshot.evidence_ids


def test_project_snapshot_respects_acl() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)

    snapshot = engine.snapshot(project, access())

    assert snapshot.evidence_ids == ()
    assert "缺少服务归属证据。" in snapshot.unresolved_items


def test_project_timeline_is_built_from_authorized_project_evidence() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)

    events = engine.timeline(project, access(ACCESS_SCOPE), limit=10)

    assert events
    assert any(event.event_type == "incident" for event in events)
    assert any(event.source.source_id == "INC-2026-001" for event in events)
    assert all(event.project == project for event in events)


def test_project_timeline_respects_acl_and_time_range() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)
    allowed_range = TimeRange(
        start=datetime(2026, 7, 19, tzinfo=timezone.utc),
        end=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )
    excluded_range = TimeRange(
        start=datetime.now(timezone.utc) + timedelta(days=1),
        end=datetime.now(timezone.utc) + timedelta(days=2),
    )

    events = engine.timeline(project, access(ACCESS_SCOPE), time_range=allowed_range)
    no_permission = engine.timeline(project, access(), time_range=allowed_range)
    excluded = engine.timeline(project, access(ACCESS_SCOPE), time_range=excluded_range)

    assert any(event.source.source_id == "INC-2026-001" for event in events)
    assert no_permission == ()
    assert excluded == ()


def test_change_impact_uses_authorized_events_and_time_range() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)
    allowed_range = TimeRange(
        start=datetime(2026, 7, 19, tzinfo=timezone.utc),
        end=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    impact = engine.change_impact(project, access(ACCESS_SCOPE), time_range=allowed_range)
    denied = engine.change_impact(project, access(), time_range=allowed_range)

    assert impact.project == project
    assert impact.related_events
    assert impact.evidence_ids
    assert any(event.event_type == "incident" for event in impact.related_events)
    assert denied.related_events == ()
    assert denied.evidence_ids == ()


def test_change_impact_returns_empty_result_outside_time_range() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)
    excluded_range = TimeRange(
        start=datetime.now(timezone.utc) + timedelta(days=1),
        end=datetime.now(timezone.utc) + timedelta(days=2),
    )

    impact = engine.change_impact(project, access(ACCESS_SCOPE), time_range=excluded_range)

    assert impact.related_events == ()
    assert impact.evidence_ids == ()
    assert "No authorized" in impact.summary


def test_knowledge_gaps_report_lists_missing_types_and_unresolved_items() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)

    report = engine.knowledge_gaps(project, access(ACCESS_SCOPE))

    assert report.project == project
    assert report.evidence_ids
    assert report.type_coverage.get("code", 0) > 0
    assert report.type_coverage.get("document", 0) > 0
    types = {gap.type.value for gap in report.gaps}
    signals = {gap.source_signal for gap in report.gaps}
    # build_demo_engine indexes code/docs/incidents only; commits/owners come from app bootstrap fixtures.
    assert "version_history" in types
    assert "owner" in types
    assert "missing_evidence_type:commit" in signals
    assert "missing_project_signal:owner" in signals
    assert all(gap.source_signal for gap in report.gaps)


def test_knowledge_gaps_report_respects_acl() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)

    report = engine.knowledge_gaps(project, access())

    assert report.evidence_ids == ()
    assert report.type_coverage == {}
    assert any(gap.type.value == "access_limited" for gap in report.gaps)
    assert all(not gap.evidence_ids for gap in report.gaps)
