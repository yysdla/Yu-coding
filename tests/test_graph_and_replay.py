import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from project_lens.context.bootstrap import LocalContextSources, build_local_context_engine
from project_lens.domain.models import ProjectAnswer, ProjectRef
from project_lens.evaluation.answer_metrics import answer_metrics
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


def test_completed_answer_has_full_citation_and_approval_metrics() -> None:
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
    run = client.post(f"/api/v1/runs/{response.json()['run_id']}/execute").json()

    metrics = answer_metrics(ProjectAnswer.model_validate(run["answer"]))
    assert metrics["citation_coverage"] == 1.0
    assert metrics["evidence_precision"] == 1.0
    assert metrics["all_actions_require_approval"] is True


def test_incident_replay_reports_completion_rate() -> None:
    app = create_app()
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    result = asyncio.run(
        replay_cases(
            app.state.run_service,
            project=project,
            user_id="replay",
            cases=[{"id": "incident-1", "question": TRACEBACK}],
        )
    )

    assert result["case_count"] == 1
    assert result["completed_count"] == 1
    assert result["completion_rate"] == 1.0
