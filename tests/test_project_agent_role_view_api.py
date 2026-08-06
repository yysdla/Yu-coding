"""HTTP RoleView replay API tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.application.project_agent_ask import (
    ProjectAgentAskRequest,
    ProjectAgentAskService,
)
from project_lens.application.project_agent_role_view import (
    ProjectAgentRoleViewService,
    RoleViewReplayRequest,
)
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.context.bootstrap import (
    build_registered_context_engine,
    default_local_project_registrations,
    to_project_registrations,
)
from project_lens.domain.models import ProjectRef
from project_lens.main import create_app
from project_lens.project_space.registry import (
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.events import InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.resolver import ProjectResolver
from tests.investigation_settings import stub_investigation_settings

ROOT = Path(__file__).resolve().parents[1]


def _build_services() -> tuple[ProjectAgentAskService, ProjectAgentRoleViewService, RunService]:
    registrations = default_local_project_registrations(ROOT)
    engine, _index = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    for space in load_project_spaces_from_dir(ROOT / "config" / "projects", base_dir=ROOT):
        if registry.get(space.tenant_id, space.project_id) is None:
            registry.register(space)

    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        lifecycle=lifecycle,
        app_settings=stub_investigation_settings(),
    )
    workflow = ProjectWorkflow(
        ProjectResolver(to_project_registrations(registrations)),
        engine,
        lifecycle=lifecycle,
    )
    run_service = RunService(
        InMemoryRunRepository(),
        workflow,
        event_sink=events,
        lifecycle=lifecycle,
        investigation_agent=agent,
        agent_mode="read_agent",
    )
    ask = ProjectAgentAskService(run_service=run_service, project_registry=registry)
    role_view = ProjectAgentRoleViewService(run_service=run_service)
    return ask, role_view, run_service


def test_http_ask_then_role_view_replay_does_not_create_new_run() -> None:
    app = create_app()
    app.state.investigation_agent._settings = stub_investigation_settings()
    client = TestClient(app)

    ask = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "这个项目是做什么的？",
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "audience": "team",
            "mode": "read_only",
        },
    )
    assert ask.status_code == 200
    ask_body = ask.json()
    assert ask_body["ok"] is True
    run_id = ask_body["run_id"]
    assert run_id

    before_events = client.get(f"/api/v1/runs/{run_id}/events")
    assert before_events.status_code == 200
    before_count = len(before_events.json())

    replay = client.post(
        f"/api/v1/project-agent/runs/{run_id}/role-view",
        json={"audience": "technical"},
    )
    assert replay.status_code == 200
    body = replay.json()
    assert body["ok"] is True
    assert body["audience"] == "technical"
    assert body["run_id"] == run_id
    assert "技术视图" in body["markdown"]
    assert body["audit_ref"]["allow_apply"] is False

    after_events = client.get(f"/api/v1/runs/{run_id}/events")
    assert after_events.status_code == 200
    assert len(after_events.json()) == before_count

    missing = client.get(f"/api/v1/runs/{uuid4()}")
    assert missing.status_code == 404


def test_http_role_view_missing_run() -> None:
    client = TestClient(create_app())
    response = client.post(
        f"/api/v1/project-agent/runs/{uuid4()}/role-view",
        json={"audience": "team"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "RUN_NOT_FOUND"
    assert body["audit_ref"]["allow_apply"] is False


def test_http_role_view_invalid_audience() -> None:
    app = create_app()
    app.state.investigation_agent._settings = stub_investigation_settings()
    client = TestClient(app)
    ask = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "介绍一下支付项目",
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
        },
    )
    run_id = ask.json()["run_id"]
    response = client.post(
        f"/api/v1/project-agent/runs/{run_id}/role-view",
        json={"audience": "not-a-role"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_AUDIENCE"


@pytest.mark.asyncio
async def test_role_view_service_replays_without_execute(monkeypatch) -> None:
    ask, role_view, run_service = _build_services()
    result = await ask.ask(
        ProjectAgentAskRequest(
            question="order_service.py 里 create_order 为什么要检查 coupon？",
            project=ProjectRef(
                tenant_id="demo",
                project_id="payment",
                service="order-service",
                environment="production",
            ),
            user_id="u1",
        )
    )
    assert result["ok"] is True
    run_id = result["run_id"]

    called = {"execute": 0}
    original = run_service.execute

    async def guarded_execute(rid):
        called["execute"] += 1
        return await original(rid)

    monkeypatch.setattr(run_service, "execute", guarded_execute)

    replay = role_view.role_view(
        RoleViewReplayRequest(run_id=UUID(run_id), audience="evidence")
    )
    assert replay["ok"] is True
    assert "证据视图" in replay["markdown"]
    assert called["execute"] == 0
