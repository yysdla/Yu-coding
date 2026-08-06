from datetime import datetime, timezone

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.domain.ops import OperationalSignal, OpsFinding, OpsQuery, OpsSignalKind
from project_lens.workflow.ops_correlation import build_ops_correlation


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def test_build_ops_correlation_extracts_route_module_symbol_and_change() -> None:
    project = _project()
    observed_at = datetime(2026, 7, 20, 10, 15, tzinfo=timezone.utc)
    query = OpsQuery(
        project=project,
        start=datetime(2026, 7, 20, 4, tzinfo=timezone.utc),
        end=datetime(2026, 7, 20, 16, tzinfo=timezone.utc),
    )
    log = OperationalSignal(
        kind=OpsSignalKind.LOG,
        project=project,
        environment="production",
        service="order-service",
        observed_at=observed_at,
        access_scope="project:payment:read",
        summary="AttributeError in create_order during checkout",
        level="error",
        trace_id="tr_checkout_500",
        labels={"route": "/orders", "http_status": "500"},
    )
    metric = OperationalSignal(
        kind=OpsSignalKind.METRIC,
        project=project,
        environment="production",
        service="order-service",
        observed_at=observed_at,
        access_scope="project:payment:read",
        summary="http_5xx_rate rose to 0.42 during checkout failures",
        metric_name="http_5xx_rate",
        metric_value=0.42,
        labels={"route": "/orders"},
    )
    trace = OperationalSignal(
        kind=OpsSignalKind.TRACE,
        project=project,
        environment="production",
        service="order-service",
        observed_at=observed_at,
        access_scope="project:payment:read",
        summary="trace failed at coupon.id access inside create_order span",
        trace_id="tr_checkout_500",
        labels={"span": "create_order"},
    )
    ops_evidence = Evidence(
        type=EvidenceType.LOG,
        project=project,
        source=SourceRef(system="ops_window", source_id="log:tr_checkout_500"),
        content=(
            "kind: log\nsummary: AttributeError in create_order during checkout\n"
            "trace_id: tr_checkout_500\nlabels: route=/orders"
        ),
        observed_at=observed_at,
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
        metadata={
            "ephemeral": True,
            "ops_kind": "log",
            "trace_id": "tr_checkout_500",
            "labels": {"route": "/orders"},
        },
    )
    code = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local_repository", source_id="src/order_service.py#L10-L20"),
        content="def create_order(request): coupon_id = request.coupon.id",
        observed_at=observed_at,
        access_scope="project:payment:read",
        content_hash="1234567890abcdefbb",
        metadata={
            "file": "src/order_service.py",
            "module": "src/order_service.py",
            "symbol": "create_order",
            "endpoint_path": "/orders",
        },
    )
    commit = Evidence(
        type=EvidenceType.COMMIT,
        project=project,
        source=SourceRef(system="git", source_id="deadbeef01"),
        content="fix coupon null guard\nsrc/order_service.py",
        observed_at=observed_at,
        access_scope="project:payment:read",
        content_hash="1234567890abcdefcc",
        metadata={"commit_sha": "deadbeef01", "files_changed": ("src/order_service.py",)},
    )
    finding = OpsFinding(
        query=query,
        signals=(log, metric, trace),
        evidence=(ops_evidence,),
        summary="window hit log=1 metric=1 trace=1",
    )

    result = build_ops_correlation(
        finding,
        evidence=(ops_evidence, code, commit),
        graph_paths=(),
        project=project,
    )

    assert result.signal_count == 3
    assert result.signal_counts_by_kind == {"log": 1, "metric": 1, "trace": 1}
    assert result.related_routes == ("/orders",)
    assert result.related_trace_ids == ("tr_checkout_500",)
    assert result.related_metrics == ("http_5xx_rate",)
    assert result.related_modules == ("src/order_service.py",)
    assert result.related_symbols == ("create_order",)
    assert any("deadbeef01" in item for item in result.related_changes)
    assert ops_evidence.id in result.evidence_ids
    assert code.id in result.evidence_ids
    assert result.facts
    assert result.inferences
    assert result.hypotheses
    assert result.recommendations
