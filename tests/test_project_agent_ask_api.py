"""HTTP one-shot Project Agent ask API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from project_lens.application.hermes_runtime import HermesRuntimeService
from project_lens.application.project_agent_ask import (
    ProjectAgentAskRequest,
    ProjectAgentAskService,
    detects_write_intent,
)
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.context.bootstrap import build_registered_context_engine, default_local_project_registrations
from project_lens.context.engine import ContextEngine
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.identity import ActorContext
from project_lens.domain.models import ProjectAnswer, ProjectRef
from project_lens.integrations.feishu.hermes_tool_loop import FeishuHermesToolLoopResult
from project_lens.main import create_app
from project_lens.project_space.models import ProjectSpace, RepositoryRef
from project_lens.project_space.policies import ProjectMemberRolePolicy, RoleKind
from project_lens.project_space.registry import (
    ProjectRegistry,
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.events import InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus

ROOT = Path(__file__).resolve().parents[1]


def _actor(*, project: ProjectRef | None = None) -> ActorContext:
    return ActorContext(
        tenant_key=(project.tenant_id if project is not None else "demo"),
        actor_id="u1",
        chat_id="test-ask-chat",
        chat_type="group",
        source="test_fixture",
        authenticated=True,
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


def _build_ask_service() -> tuple[ProjectAgentAskService, RunService]:
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
    return ask, run_service


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
    ask, run_service = _build_ask_service()
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
    assert result["run_id"]
    assert result["trace_id"]
    assert result["answer_summary"]
    assert result["audit_ref"]["allow_apply"] is False
    assert result["audit_ref"]["agent_mode"] == "hermes"
    assert result["role_views_available"]
    assert any(result["facts"]) or any(result["unknowns"])
    for fact in result["facts"]:
        assert fact["citations"]
    assert result["citations"] or result["unknowns"]
    assert run_service.get(__import__("uuid").UUID(result["run_id"])) is not None


@pytest.mark.asyncio
async def test_ask_service_project_not_found() -> None:
    ask, _run_service = _build_ask_service()
    result = await ask.ask(
        ProjectAgentAskRequest(
            question="这个项目是做什么的？",
            project=ProjectRef(tenant_id="demo", project_id="does-not-exist"),
            user_id="u1",
            actor=_actor(),
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
    ask, _run_service = _build_ask_service()
    result = await ask.ask(
        ProjectAgentAskRequest(
            question="请帮我部署到生产并重启服务",
            project=ProjectRef(tenant_id="demo", project_id="payment"),
            user_id="u1",
            actor=_actor(),
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
        public_sources=("src/",),
        members=(),
        role_policies=(
            ProjectMemberRolePolicy(
                actor_id="u1",
                project=ProjectRef(tenant_id="demo", project_id="hollow"),
                role=RoleKind.DEVELOPER,
                readable_sources=("src/",),
                allowed_tools=("search_context", "read_project_file"),
            ),
        ),
    )
    registry = ProjectRegistry((space,))
    engine = ContextEngine(InMemoryEvidenceIndex())
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
    result = await ask.ask(
        ProjectAgentAskRequest(
            question="这个 hollow 项目的核心入口在哪里？",
            project=ProjectRef(tenant_id="demo", project_id="hollow"),
            user_id="u1",
            actor=_actor(project=ProjectRef(tenant_id="demo", project_id="hollow")),
        )
    )
    assert result["ok"] is True
    assert result["audit_ref"]["allow_apply"] is False
    assert result["unknowns"]
    assert all(fact.get("citations") for fact in result["facts"])


def test_http_ask_creates_and_executes_run() -> None:
    app = create_app()
    _install_fake_hermes(app)
    client = TestClient(app)
    from tests.conftest import project_agent_headers

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
        headers=project_agent_headers(actor_id="u1", chat_id="feishu-group-1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["run_id"]
    assert body["trace_id"]
    assert body["audit_ref"]["allow_apply"] is False
    assert body["audit_ref"]["agent_mode"] == "hermes"
    for fact in body.get("facts") or []:
        assert fact.get("citations")

    # The Hermes run is visible on the standard run-event endpoint.
    events = client.get(f"/api/v1/runs/{body['run_id']}/events")
    assert events.status_code == 200
    payloads = events.json()
    assert payloads
    assert any(item["type"] in {"run_completed", "run_failed"} for item in payloads)


def test_http_ask_project_not_found() -> None:
    from tests.conftest import project_agent_headers

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "介绍一下",
            "project": {"tenant_id": "demo", "project_id": "missing-space"},
            "user_id": "u1",
        },
        headers=project_agent_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "PROJECT_NOT_FOUND"
    assert body["audit_ref"]["allow_apply"] is False


def test_http_ask_write_denied() -> None:
    from tests.conftest import project_agent_headers

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/project-agent/ask",
        json={
            "question": "请帮我部署并重启服务",
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
        },
        headers=project_agent_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "WRITE_ACTION_DENIED"
    assert body["audit_ref"]["allow_apply"] is False


def test_ask_service_is_constructed_from_hermes_runtime() -> None:
    ask, run_service = _build_ask_service()
    assert ask.run_service is run_service
