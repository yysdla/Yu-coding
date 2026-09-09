from __future__ import annotations

import pytest

from project_lens.domain.identity import ActorContext
from project_lens.integrations.mcp.handler import ask_project, call_projectlens_tool
from project_lens.integrations.mcp.server import _configured_mcp_actor


class _FakeAskService:
    def __init__(self) -> None:
        self.request = None

    async def ask(self, request):  # noqa: ANN001
        self.request = request
        return {"ok": True, "audit_ref": {"allow_apply": False}}


class _FakeToolService:
    def __init__(self) -> None:
        self.request = None

    async def call_tool(self, request):  # noqa: ANN001
        self.request = request
        return {"ok": True, "audit_ref": {"allow_apply": False}}


def _actor() -> ActorContext:
    return ActorContext(
        tenant_key="demo",
        actor_id="trusted-user",
        chat_id="trusted-chat",
        chat_type="p2p",
        source="service_token",
        authenticated=True,
    )


@pytest.mark.asyncio
async def test_mcp_ask_uses_the_trusted_actor_not_tool_parameters() -> None:
    service = _FakeAskService()

    result = await ask_project(
        service,
        question="What is the project owner?",
        project_id="payment",
        tenant_id="demo",
        user_id="untrusted-user",
        channel_id="untrusted-chat",
        actor=_actor(),
    )

    assert result["ok"] is True
    assert service.request.actor == _actor()
    assert service.request.user_id == "untrusted-user"
    assert service.request.channel_id == "untrusted-chat"


@pytest.mark.asyncio
async def test_mcp_formal_tool_uses_the_trusted_actor_not_tool_parameters() -> None:
    service = _FakeToolService()

    result = await call_projectlens_tool(
        service,
        tool_name="projectlens_search_context",
        project_id="payment",
        tenant_id="demo",
        user_id="untrusted-user",
        chat_id="untrusted-chat",
        arguments={"query": "owner"},
        actor=_actor(),
    )

    assert result["ok"] is True
    assert service.request.user_id == "trusted-user"
    assert service.request.chat_id == "trusted-chat"
    assert service.request.chat_type == "p2p"
    assert service.request.identity_source == "service_token"


@pytest.mark.asyncio
async def test_mcp_calls_fail_closed_without_a_trusted_actor() -> None:
    ask = await ask_project(
        _FakeAskService(),
        question="What is the project owner?",
        project_id="payment",
    )
    tool = await call_projectlens_tool(
        _FakeToolService(),
        tool_name="projectlens_search_context",
        project_id="payment",
    )

    assert ask["error_code"] == "TRUSTED_ACTOR_REQUIRED"
    assert tool["error_code"] == "TRUSTED_ACTOR_REQUIRED"


def test_mcp_server_builds_actor_only_from_deployment_settings(monkeypatch) -> None:  # noqa: ANN001
    from project_lens import config

    monkeypatch.setattr(config.settings, "mcp_trusted_tenant_id", "demo")
    monkeypatch.setattr(config.settings, "mcp_trusted_actor_id", "mcp-service")
    monkeypatch.setattr(config.settings, "mcp_trusted_chat_id", "mcp-chat")
    monkeypatch.setattr(config.settings, "mcp_trusted_chat_type", "group")

    actor = _configured_mcp_actor()

    assert actor is not None
    assert actor.actor_id == "mcp-service"
    assert actor.chat_id == "mcp-chat"
    assert actor.source == "service_token"
