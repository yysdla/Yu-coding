"""ContextPrompt wiring and ModelAdapter entry-point tests."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from project_lens.domain.models import ProjectRef
from project_lens.main import create_app
from project_lens.runtime.lifecycle import LifecycleEventType
from project_lens.workflow.context_prompt import render_context_prompt
from project_lens.workflow.model_adapter import StubModelAdapter
from tests.test_context_prompt import _pack


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


@pytest.mark.asyncio
async def test_stub_adapter_accepts_context_prompt_only() -> None:
    prompt = render_context_prompt(_pack())
    result = await StubModelAdapter().prepare(prompt)
    assert result.used_prompt is True
    assert result.adapter == "model_adapter.v1"
    assert result.provider == "stub.v1"
    assert result.allow_apply is False
    assert result.section_layers == ("L0", "L1", "L2", "L3", "L4", "L5")
    assert result.message_count >= 1
    assert result.usage["total_tokens"] > 0
    assert result.audit_refs()["allow_apply"] is False


def test_workflow_run_renders_prompt_and_uses_stub_adapter() -> None:
    app = create_app()
    client = TestClient(app)
    created = client.post(
        "/api/v1/runs",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "question": "这个项目的架构是什么？",
        },
    )
    run_id = created.json()["run_id"]
    executed = client.post(f"/api/v1/runs/{run_id}/execute")
    assert executed.status_code == 200

    workflow = app.state.run_service._workflow
    assert workflow.last_context_pack is not None
    assert workflow.last_context_prompt is not None
    assert workflow.last_model_adapter_result is not None
    assert workflow.last_model_adapter_result.provider == "stub.v1"
    assert "allow_apply=False" in workflow.last_context_prompt.as_text()
    assert "allow_apply=True" not in workflow.last_context_prompt.as_text()

    prompt_events = [
        event
        for event in app.state.lifecycle_bus.all()
        if event.type == LifecycleEventType.CONTEXT_PROMPT_RENDERED
    ]
    assert prompt_events
    payload = prompt_events[-1].payload
    assert payload["context_prompt"]["allow_apply"] is False
    assert payload["model_adapter"]["used_prompt"] is True
    assert payload["model_adapter"]["usage"]["total_tokens"] > 0
    full_bodies = [item.content for item in workflow.last_context_pack.evidence]
    serialized = str(payload)
    for body in full_bodies:
        if len(body) > 80:
            assert body not in serialized


@pytest.mark.asyncio
async def test_analyzing_event_includes_context_prompt_audit_refs() -> None:
    app = create_app()
    client = TestClient(app)
    created = client.post(
        "/api/v1/runs",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "channel_id": "chat-prompt",
            "question": "这个项目的架构是什么？",
        },
    )
    run_id = UUID(created.json()["run_id"])
    session = app.state.conversation_service.get_or_create(
        tenant_id="demo",
        chat_id="chat-prompt",
        user_id="u1",
        project=_project(),
    )
    completed = await app.state.run_service.execute(run_id, session=session)
    assert completed is not None
    events = app.state.run_service.events(run_id)
    analyzing = next(event for event in events if event.payload.get("status") == "analyzing")
    assert analyzing.payload["context_prompt"]["renderer"] == "context_prompt.v1"
    assert analyzing.payload["model_adapter"]["provider"] == "stub.v1"
    assert analyzing.payload["context_pack"]["session_id"] == str(session.session_id)
