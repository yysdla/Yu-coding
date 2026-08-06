from datetime import datetime, timezone
from pathlib import Path

from project_lens.context.bootstrap import default_local_project_registrations, build_registered_context_engine
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.context.ops.store import load_ops_signals_file
from project_lens.domain.models import EvidenceType, ProjectRef
from project_lens.domain.ops import OpsQuery, OpsSignalKind
from project_lens.workflow.analysis import AnalysisAgent
from project_lens.workflow.ops_bridge import maybe_collect_ops_finding
from project_lens.workflow.skills import ProjectSkill

ROOT = Path(__file__).parents[1]
DEMO = ROOT / "examples" / "payment_service"
ACCESS = "project:payment:read"


def test_ops_signals_load_from_fixture_without_rag_indexing() -> None:
    signals = load_ops_signals_file(DEMO / "fixtures" / "ops_signals.json")
    assert {item.kind for item in signals} == {
        OpsSignalKind.LOG,
        OpsSignalKind.METRIC,
        OpsSignalKind.TRACE,
    }
    registrations = default_local_project_registrations(ROOT)
    engine, index = build_registered_context_engine(registrations)
    # Ops signals stay in the ops store, not the long-lived evidence index.
    assert all(item.source.system != "ops_window" for item in index.all())
    assert engine.ops_store.all()


def test_query_ops_respects_acl_and_time_window() -> None:
    engine, _index = build_registered_context_engine(
        default_local_project_registrations(ROOT)
    )
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    allowed = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS}),
    )
    denied = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({"project:other:read"}),
    )
    query = OpsQuery(
        project=project,
        start=datetime(2026, 7, 20, 9, tzinfo=timezone.utc),
        end=datetime(2026, 7, 20, 12, tzinfo=timezone.utc),
    )
    finding = engine.query_ops(query, allowed)
    empty = engine.query_ops(query, denied)
    assert finding.signals
    assert finding.evidence
    assert all(item.metadata.get("ephemeral") is True for item in finding.evidence)
    assert {item.type for item in finding.evidence} >= {EvidenceType.LOG, EvidenceType.METRIC}
    assert empty.signals == ()


def test_incident_analysis_uses_ops_window_facts() -> None:
    engine, _index = build_registered_context_engine(
        default_local_project_registrations(ROOT)
    )
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS}),
    )
    question = "最近故障 checkout HTTP 500"
    bundle = engine.search(
        ContextQuery(text=question, project=project, limit=12),
        access,
    )
    finding, enriched = maybe_collect_ops_finding(
        engine,
        project=project,
        access=access,
        skill=ProjectSkill.INCIDENT_DIAGNOSIS,
        question=question,
        bundle=bundle,
    )
    assert finding is not None
    assert finding.signals
    assert any(item.source.system == "ops_window" for item in enriched.evidence)

    result = AnalysisAgent().analyze(
        question,
        enriched,
        skill=ProjectSkill.INCIDENT_DIAGNOSIS,
        ops_finding=finding,
    )
    assert any("运维时间窗信号" in claim.text for claim in result.candidates)
    assert not any("缺少实时监控证据" in item for item in result.unknowns)
