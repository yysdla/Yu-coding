"""HTTP one-shot Project Agent ask API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.application.project_agent_ask import (
    ProjectAgentAskRequest,
    ProjectAgentAskService,
    detects_write_intent,
)
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.context.bootstrap import (
    build_registered_context_engine,
    default_local_project_registrations,
    to_project_registrations,
)
from project_lens.context.engine import ContextEngine
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import ProjectRef
from project_lens.main import create_app
from project_lens.project_space.models import ProjectSpace, RepositoryRef
from project_lens.project_space.registry import (
    ProjectRegistry,
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.events import AgentEventType, InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.resolver import ProjectResolver
from tests.investigation_settings import stub_investigation_settings

ROOT = Path(__file__).resolve().parents[1]


def _build_ask_service() -> tuple[ProjectAgentAskService, ProjectInvestigationAgent, RunService]:
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
    return ask, agent, run_service


def test_detects_write_intent_conservative() -> None:
    assert detects_write_intent("请帮我部署到生产环境")
    assert detects_write_intent("重启服务 order-service")
    assert detects_write_intent("create a PR for this fix")
    assert detects_write_intent("apply the patch now")
    assert not detects_write_intent("介绍一下部署流程文档")
    assert not detects_write_intent("这个项目是做什么的")
    assert not detects_write_intent("订单创建入口在哪个文件")


@pytest.mark.asyncio
async def test_ask_service_free_question_returns_cited_envelope() -> None:
    ask, agent, run_service = _build_ask_service()
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
    assert result["run_id"]
    assert result["trace_id"]
    assert result["answer_summary"]
    assert result["audit_ref"]["allow_apply"] is False
    assert result["audit_ref"]["agent_mode"] == "read_agent"
    assert result["role_views_available"]
    assert any(result["facts"]) or any(result["unknowns"])
    for fact in result["facts"]:
        assert fact["citations"]
    assert result["citations"] or result["unknowns"]
    assert agent.last_tool_names

    run_id = result["run_id"]
    from uuid import UUID

    events = run_service.events(UUID(run_id))
    tool_events = [
        event
        for event in events
        if event.type == AgentEventType.TOOL_COMPLETED
        or (
            event.type == AgentEventType.LIFECYCLE
            and (event.payload or {}).get("lifecycle") == "tool.completed"
        )
    ]
    assert tool_events


@pytest.mark.asyncio
async def test_ask_service_project_not_found() -> None:
    ask, _agent, _run_service = _build_ask_service()
    result = await ask.ask(
        ProjectAgentAskRequest(
            question="这个项目是做什么的？",
            project=ProjectRef(tenant_id="demo", project_id="does-not-exist"),
            user_id="u1",
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "PROJECT_NOT_FOUND"
    assert result["retryable"] is False
    assert "registered" in result["agent_recovery_hint"].lower() or "ProjectSpace" in result[
        "message"
    ]
    assert result["audit_ref"]["allow_apply"] is False


@pytest.mark.asyncio
async def test_ask_service_write_intent_denied() -> None:
    ask, _agent, _run_service = _build_ask_service()
    result = await ask.ask(
        ProjectAgentAskRequest(
            question="请帮我部署到生产并重启服务",
            project=ProjectRef(tenant_id="demo", project_id="payment"),
            user_id="u1",
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "WRITE_ACTION_DENIED"
    assert result["audit_ref"]["allow_apply"] is False
    assert "unknowns" in result


@pytest.mark.asyncio
async def test_ask_service_no_evidence_returns_unknowns() -> None:
    empty_root = ROOT / "examples" / "payment_service" / "knowledge" / ".empty_agent_ask"
    empty_root.mkdir(parents=True, exist_ok=True)
    space = ProjectSpace(
        tenant_id="demo",
        project_id="hollow",
        display_name="hollow",
        repositories=(RepositoryRef(name="hollow", path=empty_root),),
        access_scope="project:hollow:read",
        file_allowlist=("src/",),
    )
    registry = ProjectRegistry((space,))
    engine = ContextEngine(InMemoryEvidenceIndex())
    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        lifecycle=lifecycle,
        app_settings=stub_investigation_settings(),
    )
    # Minimal workflow for RunService constructor (not used in read_agent mode).
    registrations = default_local_project_registrations(ROOT)
    payment_engine, _ = build_registered_context_engine(registrations)
    workflow = ProjectWorkflow(
        ProjectResolver(to_project_registrations(registrations)),
        payment_engine,
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
    result = await ask.ask(
        ProjectAgentAskRequest(
            question="这个 hollow 项目的核心入口在哪里？",
            project=ProjectRef(tenant_id="demo", project_id="hollow"),
            user_id="u1",
        )
    )
    assert result["ok"] is True
    assert result["audit_ref"]["allow_apply"] is False
    assert result["unknowns"]
    assert all(fact.get("citations") for fact in result["facts"])


def test_http_ask_creates_and_executes_run() -> None:
    app = create_app()
    app.state.investigation_agent._settings = stub_investigation_settings()
    client = TestClient(app)
    response = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "这个项目是做什么的？核心服务有哪些？",
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "feishu-user-1",
            "channel_id": "feishu-group-1",
            "audience": "team",
            "mode": "read_only",
            "format": "concise",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["run_id"]
    assert body["trace_id"]
    assert body["audit_ref"]["allow_apply"] is False
    assert body["audit_ref"]["agent_mode"] == "read_agent"
    for fact in body.get("facts") or []:
        assert fact.get("citations")

    # Tool audit visible on run events
    events = client.get(f"/api/v1/runs/{body['run_id']}/events")
    assert events.status_code == 200
    payloads = events.json()
    assert payloads
    toolish = [
        item
        for item in payloads
        if item["type"] in {"tool_completed", "lifecycle"}
        and (
            (item.get("payload") or {}).get("tool")
            or str((item.get("payload") or {}).get("lifecycle") or "").startswith("tool.")
        )
    ]
    assert toolish


def test_http_ask_project_not_found() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "介绍一下",
            "project": {"tenant_id": "demo", "project_id": "missing-space"},
            "user_id": "u1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "PROJECT_NOT_FOUND"
    assert body["audit_ref"]["allow_apply"] is False


def test_http_ask_write_denied() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "请帮我部署并重启服务",
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "WRITE_ACTION_DENIED"
    assert body["audit_ref"]["allow_apply"] is False


def test_ask_service_rejects_non_read_agent_run_service() -> None:
    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    run_service = RunService(
        InMemoryRunRepository(),
        ProjectWorkflow(ProjectResolver(to_project_registrations(registrations)), engine),
        agent_mode="workflow",
    )
    with pytest.raises(ValueError, match="read_agent"):
        ProjectAgentAskService(run_service=run_service, project_registry=registry)
