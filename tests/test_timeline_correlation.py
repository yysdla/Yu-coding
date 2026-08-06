from datetime import datetime, timezone

from project_lens.context.timeline import build_timeline_events
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.domain.ops import OperationalSignal, OpsFinding, OpsQuery, OpsSignalKind
from project_lens.workflow.timeline_correlation import build_timeline_correlation


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _release(project: ProjectRef, observed_at: datetime) -> Evidence:
    return Evidence(
        type=EvidenceType.TASK,
        project=project,
        source=SourceRef(system="release_notes", source_id="REL-2026-07-20"),
        content="Release: checkout hotfix\nlatest release version: 2026.07.20",
        observed_at=observed_at,
        access_scope="project:payment:read",
        content_hash="release20260720abcdef",
        metadata={
            "kind": "release",
            "release_id": "REL-2026-07-20",
            "version": "2026.07.20",
            "service": "order-service",
        },
    )


def _ops_finding(project: ProjectRef, observed_at: datetime) -> OpsFinding:
    query = OpsQuery(
        project=project,
        start=datetime(2026, 7, 20, 0, tzinfo=timezone.utc),
        end=datetime(2026, 7, 20, 23, tzinfo=timezone.utc),
    )
    signal = OperationalSignal(
        kind=OpsSignalKind.LOG,
        project=project,
        environment="production",
        service="order-service",
        observed_at=observed_at,
        access_scope="project:payment:read",
        summary="AttributeError in create_order after checkout request",
        trace_id="tr_checkout_500",
        labels={"route": "/orders"},
    )
    ops_evidence = Evidence(
        type=EvidenceType.LOG,
        project=project,
        source=SourceRef(system="ops_window", source_id="log:tr_checkout_500"),
        content="kind: log\nsummary: AttributeError in create_order\ntrace_id: tr_checkout_500",
        observed_at=observed_at,
        access_scope="project:payment:read",
        content_hash="opswindow20260720ab",
        metadata={"ephemeral": True, "ops_kind": "log", "trace_id": "tr_checkout_500"},
    )
    return OpsFinding(
        query=query,
        signals=(signal,),
        summary="window hit log=1",
        evidence=(ops_evidence,),
    )


def test_timeline_correlation_marks_ops_after_release() -> None:
    project = _project()
    release = _release(project, datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
    finding = _ops_finding(project, datetime(2026, 7, 20, 10, 15, tzinfo=timezone.utc))
    timeline = build_timeline_events((release,), project=project)

    result = build_timeline_correlation(
        timeline=timeline,
        ops_finding=finding,
        evidence=(release, *finding.evidence),
        project=project,
    )

    assert result is not None
    assert result.nearest_release_id == "REL-2026-07-20"
    assert result.nearest_release_delta_minutes == 15
    assert result.ops_after_release is True
    assert any("ops_after_release" in item for item in result.inferences)
    assert any("temporal proximity is not causality" in item for item in result.hypotheses)
    assert release.id in result.evidence_ids
    assert finding.evidence[0].id in result.evidence_ids


def test_timeline_correlation_marks_release_after_ops_as_likely_hotfix() -> None:
    project = _project()
    release = _release(project, datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc))
    finding = _ops_finding(project, datetime(2026, 7, 20, 10, 15, tzinfo=timezone.utc))
    timeline = build_timeline_events((release,), project=project)

    result = build_timeline_correlation(
        timeline=timeline,
        ops_finding=finding,
        evidence=(release, *finding.evidence),
        project=project,
    )

    assert result is not None
    assert result.nearest_release_id == "REL-2026-07-20"
    assert result.nearest_release_delta_minutes == 225
    assert result.ops_after_release is False
    assert any("release_after_ops" in item for item in result.inferences)
    assert any("hotfix" in item.lower() for item in result.inferences)
    assert result.facts
    assert result.recommendations
