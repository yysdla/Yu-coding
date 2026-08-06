"""ReadContextGateway: ACL + audit facade over ContextEngine read tools."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project_lens.context.models import AccessContext, ContextQuery
from project_lens.domain.models import ProjectRef
from project_lens.graph.query import GraphQuery
from project_lens.main import create_app
from project_lens.runtime.policy import RiskClass
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.runtime.tool_specs import ToolLane, assert_tool_callable, tool_policy_hash

ROOT = Path(__file__).parents[1]


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _access() -> AccessContext:
    return AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({"project:payment:read"}),
    )


def test_read_gateway_refuses_allow_apply() -> None:
    app = create_app()
    with pytest.raises(ValueError, match="allow_apply"):
        ReadContextGateway(app.state.context_engine, allow_apply=True)


def test_read_gateway_audits_search_and_never_apply() -> None:
    app = create_app()
    gateway = ReadContextGateway(app.state.context_engine)
    bundle = gateway.search_context(
        ContextQuery(text="coupon AttributeError", project=_project(), limit=5),
        _access(),
    )
    assert bundle.hits
    names = [event.tool_name for event in gateway.audit_events]
    assert names == ["search_context"]
    assert all(event.risk_class == RiskClass.READ for event in gateway.audit_events)
    assert gateway.audit_summary()["allow_apply"] is False
    assert gateway.audit_summary()["apply_events"] == []


def test_read_gateway_authorized_and_gaps_and_graph() -> None:
    app = create_app()
    gateway = ReadContextGateway(app.state.context_engine)
    project = _project()
    access = _access()
    gaps = gateway.list_knowledge_gaps(project, access)
    evidence = gateway.authorized_evidence(project, access, limit=20)
    paths = gateway.query_graph(
        project,
        access,
        GraphQuery(relation="responsible_for", limit=5),
    )
    assert gaps.gaps is not None
    assert evidence
    assert isinstance(paths, tuple)
    names = {event.tool_name for event in gateway.audit_events}
    assert {
        "list_knowledge_gaps",
        "authorized_evidence",
        "query_graph",
    } <= names
    assert_tool_callable("authorized_evidence", allow_apply=False)
    assert all(spec.category == ToolLane.READ for spec in gateway.list_read_specs())


def test_workflow_collect_uses_read_gateway_audit() -> None:
    app = create_app()
    client = TestClient(app)
    create = client.post(
        "/api/v1/runs",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "question": "介绍一下这个项目",
        },
    )
    run_id = create.json()["run_id"]
    executed = client.post(f"/api/v1/runs/{run_id}/execute")
    assert executed.status_code == 200
    gateway = app.state.run_service._workflow.read_gateway
    names = [event.tool_name for event in gateway.audit_events]
    assert "list_knowledge_gaps" in names
    assert "authorized_evidence" in names
    assert "query_graph" in names
    assert all(event.risk_class != RiskClass.APPLY for event in gateway.audit_events)
    pack = app.state.run_service._workflow.last_context_pack
    assert pack is not None
    assert pack.provenance.evidence_source == "read_context_gateway"
    # Tool registry fingerprint remains stable within process.
    assert pack.anchors.tool_policy_hash == tool_policy_hash()
