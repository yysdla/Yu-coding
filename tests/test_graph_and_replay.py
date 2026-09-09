import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project_lens.context.bootstrap import LocalContextSources, build_local_context_engine
from project_lens.domain.models import ProjectRef
from project_lens.evaluation.replay import replay_cases
from project_lens.graph import EvidenceGraphBuilder
from project_lens.graph.models import GraphNodeKind
from project_lens.main import create_app


TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def test_demo_evidence_graph_contains_service_symbol_and_relations() -> None:
    root = Path(__file__).parents[1]
    project_root = root / "examples" / "payment_service"
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    _, index = build_local_context_engine(
        LocalContextSources(
            repository_root=project_root / "src",
            documents_root=project_root / "knowledge",
            incidents_file=project_root / "knowledge" / "incidents.json",
        ),
        project=project,
        access_scope="project:payment:read",
    )
    graph = EvidenceGraphBuilder().build(index.all())

    kinds = {node.kind for node in graph.nodes}
    assert GraphNodeKind.EVIDENCE in kinds
    assert GraphNodeKind.SERVICE in kinds
    assert GraphNodeKind.SYMBOL in kinds
    assert any(edge.relation == "relates_to" for edge in graph.edges)
    assert any(
        node.label == "create_order" and node.kind == GraphNodeKind.SYMBOL
        for node in graph.nodes
    )


def test_legacy_run_execute_is_blocked_for_hermes_app() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/runs",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "evaluator",
            "question": TRACEBACK,
        },
    )
    executed = client.post(f"/api/v1/runs/{response.json()['run_id']}/execute")
    assert executed.status_code == 409
    assert "Hermes" in executed.json()["detail"]


def test_incident_replay_reports_hermes_execute_block() -> None:
    app = create_app()
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    with pytest.raises(RuntimeError, match="Hermes"):
        asyncio.run(
            replay_cases(
                app.state.run_service,
                project=project,
                user_id="replay",
                cases=[{"id": "incident-1", "question": TRACEBACK}],
            )
        )
