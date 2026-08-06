from pathlib import Path

import pytest

from project_lens.domain.models import AgentRun, RunStatus
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.resolver import ProjectResolver
from tests.context_helpers import build_demo_engine


@pytest.mark.asyncio
async def test_incident_workflow_emits_projectops_correlation_claims() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    engine, _index, project = build_demo_engine(root, include_git_and_feishu=True)
    workflow = ProjectWorkflow(ProjectResolver((project,)), engine)
    run = AgentRun(
        project=project,
        user_id="u1",
        question="incident checkout HTTP 500 /orders create_order",
    )
    transitions: list[tuple[RunStatus, dict[str, object]]] = []

    async def record(status: RunStatus, payload: dict[str, object]) -> None:
        transitions.append((status, payload))

    answer = await workflow.execute(run, record)

    assert answer.skill == "incident_diagnosis"
    assert any(item.source.system == "ops_window" for item in answer.evidence)
    assert all(
        item.metadata.get("ephemeral") is True
        for item in answer.evidence
        if item.source.system == "ops_window"
    )
    projectops_claims = [
        claim.text for claim in answer.claims if "ProjectOps 相关性分析" in claim.text
    ]
    assert projectops_claims
    projectops_text = "\n".join(projectops_claims)
    assert "/orders" in projectops_text
    assert "create_order" in projectops_text
    timeline_claims = [
        claim.text for claim in answer.claims if "ProjectOps timeline correlation" in claim.text
    ]
    assert timeline_claims
    timeline_text = "\n".join(timeline_claims)
    assert "REL-2026-07-20" in timeline_text
    assert "release_after_ops" in timeline_text
    graph_claims = [
        claim.text for claim in answer.claims if "项目关系图显示" in claim.text
    ]
    assert any("/orders" in item for item in graph_claims)
    assert any("create_order" in item for item in graph_claims)
    assert any(
        "ProjectOps 只读排查" in action.title
        and action.requires_approval is True
        for action in answer.recommended_actions
    )
    analyzing_payload = next(
        payload for status, payload in transitions if status == RunStatus.ANALYZING
    )
    assert analyzing_payload["ops_signal_count"] == 3
