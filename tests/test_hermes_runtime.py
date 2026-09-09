from __future__ import annotations

from uuid import UUID

import pytest

from project_lens.application.hermes_runtime import HermesRuntimeService
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.domain.identity import ActorContext
from project_lens.domain.models import ProjectAnswer, ProjectRef, RunStatus
from project_lens.integrations.feishu.hermes_tool_loop import FeishuHermesToolLoopResult
from project_lens.project_space.policies import (
    AnswerDepth,
    EffectiveAccessScope,
    RoleKind,
    VisibilityLevel,
)
from project_lens.runtime.events import InMemoryEventSink


class _FakeHermesBridge:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[dict[str, object]] = []

    async def answer(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if not self.ok:
            return FeishuHermesToolLoopResult(
                ok=False,
                envelope={"audit_ref": {"error": "runner unavailable"}},
                loop_id=kwargs["loop_id"],
                trace_id=kwargs["trace_id"],
            )
        project = kwargs["project"]
        assert isinstance(project, ProjectRef)
        answer = ProjectAnswer(
            project=project,
            status="unknown",
            business_summary="Evidence is insufficient.",
            technical_summary="Evidence is insufficient.",
            unknowns=("Need cited evidence.",),
        )
        return FeishuHermesToolLoopResult(
            ok=True,
            envelope={"ok": True, "audit_ref": {"allow_apply": False}},
            tool_names=("projectlens_search_context",),
            loop_id=kwargs["loop_id"],
            trace_id=kwargs["trace_id"],
            verified_answer=answer,
        )


def _binding() -> tuple[ProjectRef, ActorContext, EffectiveAccessScope]:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    actor = ActorContext(
        tenant_key="demo",
        actor_id="user-1",
        chat_id="chat-1",
        chat_type="group",
        source="test_fixture",
        authenticated=True,
    )
    scope = EffectiveAccessScope(
        project=project,
        actor_id=actor.actor_id,
        chat_id=actor.chat_id,
        role=RoleKind.DEVELOPER,
        readable_sources=("src/",),
        allowed_tools=("search_context",),
        forbidden_sources=(),
        answer_depth=AnswerDepth.BALANCED,
        answer_style="technical",
        visibility_level=VisibilityLevel.TEAM_SHARED,
        identity_source=actor.source,
        chat_type=actor.chat_type,
    )
    return project, actor, scope


@pytest.mark.asyncio
async def test_hermes_runtime_creates_one_bound_run_before_starting_loop() -> None:
    events = InMemoryEventSink()
    runs = RunService(
        InMemoryRunRepository(), event_sink=events, agent_mode="hermes"
    )
    bridge = _FakeHermesBridge()
    runtime = HermesRuntimeService(run_service=runs, bridge=bridge)  # type: ignore[arg-type]
    project, actor, scope = _binding()

    pending = runtime.prepare(
        project=project,
        actor=actor,
        scope=scope,
        question="What is the owner?",
        entry_mode="http_ask",
    )
    assert pending.run.runtime == "hermes"
    assert bridge.calls == []

    execution = await runtime.execute_prepared(pending)

    assert execution.ok is True
    assert execution.run.runtime == "hermes"
    assert execution.run.entry_mode == "http_ask"
    assert execution.run.hermes_loop_id == execution.loop_id
    assert execution.run.context_hash
    assert execution.run.status == RunStatus.COMPLETED
    assert bridge.calls[0]["run_id"] == execution.run.id
    assert bridge.calls[0]["loop_id"] == execution.loop_id
    assert bridge.calls[0]["trace_id"] == execution.run.trace_id
    assert execution.run.runtime_access is not None
    assert execution.run.runtime_access["actor_id"] == actor.actor_id


@pytest.mark.asyncio
async def test_hermes_runtime_fails_closed_when_runner_fails() -> None:
    runs = RunService(InMemoryRunRepository(), agent_mode="hermes")
    runtime = HermesRuntimeService(
        run_service=runs, bridge=_FakeHermesBridge(ok=False)  # type: ignore[arg-type]
    )
    project, actor, scope = _binding()

    execution = await runtime.execute(
        project=project,
        actor=actor,
        scope=scope,
        question="What is the owner?",
        entry_mode="mcp",
    )

    assert execution.ok is False
    assert execution.run.runtime == "hermes"
    assert execution.run.status == RunStatus.FAILED
    assert execution.run.answer is None
    assert "runner unavailable" in (execution.run.error or "")


def test_http_ask_uses_the_shared_hermes_runtime() -> None:
    from fastapi.testclient import TestClient

    from project_lens.main import create_app
    from tests.conftest import project_agent_headers

    app = create_app()
    bridge = app.state.feishu_hermes_tool_loop_bridge

    async def answer(**kwargs):  # noqa: ANN003
        project = kwargs["project"]
        assert isinstance(project, ProjectRef)
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

    bridge.answer = answer  # type: ignore[method-assign]
    response = TestClient(app).post(
        "/api/v1/project-agent/ask",
        json={
            "question": "What is the project owner?",
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "untrusted-body-user",
            "channel_id": "untrusted-body-chat",
            "mode": "read_only",
        },
        headers=project_agent_headers(actor_id="u1", chat_id="chat-1"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["audit_ref"]["runtime"] == "hermes"
    assert body["audit_ref"]["agent_mode"] == "hermes"
    run = app.state.run_service.get(UUID(body["run_id"]))
    assert run is not None
    assert run.user_id == "u1"
    assert run.channel_id == "chat-1"
    assert run.runtime == "hermes"
