"""HTTP RoleView replay API tests."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from project_lens.application.hermes_runtime import HermesRuntimeService
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
)
from project_lens.domain.identity import ActorContext
from project_lens.domain.models import ProjectAnswer, ProjectRef
from project_lens.integrations.feishu.hermes_tool_loop import FeishuHermesToolLoopResult
from project_lens.main import create_app
from project_lens.project_space.registry import (
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.events import InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus
from tests.conftest import project_agent_headers

ROOT = Path(__file__).resolve().parents[1]


def _actor() -> ActorContext:
    return ActorContext(
        tenant_key="demo", actor_id="u1", chat_id="role-view-chat",
        chat_type="group", source="test_fixture", authenticated=True,
    )


class _FakeHermesBridge:
    async def answer(self, **kwargs):  # noqa: ANN003
        project = kwargs["project"]
        return FeishuHermesToolLoopResult(
            ok=True,
            envelope={"ok": True, "audit_ref": {"allow_apply": False}},
            tool_names=("projectlens_search_context",),
            loop_id=kwargs["loop_id"],
            trace_id=kwargs["trace_id"],
            verified_answer=ProjectAnswer(
                project=project,
                status="unknown",
                business_summary="Evidence is insufficient.",
                technical_summary="Evidence is insufficient.",
                unknowns=("Need cited evidence.",),
            ),
        )


def _install_fake_hermes(app) -> None:  # noqa: ANN001
    app.state.feishu_hermes_tool_loop_bridge.answer = _FakeHermesBridge().answer


def _build_services() -> tuple[ProjectAgentAskService, ProjectAgentRoleViewService, RunService]:
    registrations = default_local_project_registrations(ROOT)
    engine, _index = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    for space in load_project_spaces_from_dir(ROOT / "config" / "projects", base_dir=ROOT):
        registry.upsert(space)

    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    run_service = RunService(
        InMemoryRunRepository(),
        event_sink=events,
        lifecycle=lifecycle,
        agent_mode="hermes",
    )
    ask = ProjectAgentAskService(
        hermes_runtime=HermesRuntimeService(
            run_service=run_service, bridge=_FakeHermesBridge()  # type: ignore[arg-type]
        ),
        project_registry=registry,
    )
    role_view = ProjectAgentRoleViewService(run_service=run_service)
    return ask, role_view, run_service


def test_http_ask_then_role_view_replay_does_not_create_new_run() -> None:
    app = create_app()
    _install_fake_hermes(app)
    client = TestClient(app)

    headers = project_agent_headers(actor_id="u1", chat_id="role-view-chat")
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
        headers=headers,
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
        headers=headers,
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
    _install_fake_hermes(app)
    client = TestClient(app)
    headers = project_agent_headers(actor_id="u1", chat_id="role-view-invalid")
    ask = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "介绍一下支付项目",
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
        },
        headers=headers,
    )
    run_id = ask.json()["run_id"]
    response = client.post(
        f"/api/v1/project-agent/runs/{run_id}/role-view",
        json={"audience": "not-a-role"},
        headers=headers,
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
            actor=_actor(),
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
