"""Pluggable ContextPrompt ModelProvider tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from project_lens.config import Settings
from project_lens.main import create_app
from project_lens.workflow.context_prompt import render_context_prompt
from project_lens.workflow.model_adapter import ProviderModelAdapter
from project_lens.workflow.providers import (
    InternalProvider,
    LocalProvider,
    OpenAIProvider,
    ProviderKind,
    ProviderRuntimeConfig,
    StubProvider,
    build_model_provider,
    create_default_model_adapter,
    create_model_adapter_from_settings,
    provider_config_from_settings,
)
from tests.test_context_prompt import _pack


@pytest.mark.asyncio
async def test_default_provider_is_stub() -> None:
    settings = Settings(model_provider="stub")
    config = provider_config_from_settings(settings)
    assert config.kind == ProviderKind.STUB
    provider = build_model_provider(config)
    assert isinstance(provider, StubProvider)
    adapter = create_model_adapter_from_settings(settings)
    assert isinstance(adapter.provider, StubProvider)
    prompt = render_context_prompt(_pack())
    result = await adapter.prepare(prompt)
    assert result.provider == "stub.v1"
    assert result.provider_kind == "stub"
    assert result.status == "ok"
    assert result.usage["total_tokens"] > 0


@pytest.mark.asyncio
async def test_provider_timeout_is_recorded() -> None:
    prompt = render_context_prompt(_pack())
    adapter = ProviderModelAdapter(
        StubProvider(simulate_timeouts=2),
        config=ProviderRuntimeConfig(
            kind=ProviderKind.STUB,
            timeout_seconds=0.05,
            max_retries=0,
        ),
    )
    result = await adapter.prepare(prompt)
    assert result.timed_out is True
    assert result.status == "timeout"
    assert result.attempts
    assert result.attempts[0].timed_out is True
    assert result.audit_refs()["timed_out"] is True


@pytest.mark.asyncio
async def test_provider_retry_is_recorded() -> None:
    prompt = render_context_prompt(_pack())
    adapter = ProviderModelAdapter(
        StubProvider(simulate_timeouts=1),
        config=ProviderRuntimeConfig(
            kind=ProviderKind.STUB,
            timeout_seconds=0.05,
            max_retries=1,
        ),
    )
    result = await adapter.prepare(prompt)
    assert result.status == "ok"
    assert result.retries == 1
    assert len(result.attempts) == 2
    assert result.attempts[0].timed_out is True
    assert result.attempts[1].ok is True
    assert result.audit_refs()["retries"] == 1


@pytest.mark.asyncio
async def test_usage_enters_audit_refs_without_evidence_body() -> None:
    pack = _pack(long_evidence=True)
    prompt = render_context_prompt(pack, evidence_snippet_chars=48)
    result = await create_default_model_adapter().prepare(prompt)
    refs = result.audit_refs()
    assert "usage" in refs
    assert refs["usage"]["prompt_tokens"] > 0
    assert refs["usage"]["total_tokens"] >= refs["usage"]["prompt_tokens"]
    full_body = pack.evidence[0].content
    assert full_body not in str(refs)
    assert full_body not in prompt.as_text()


@pytest.mark.asyncio
async def test_reserved_providers_do_not_call_live_llm() -> None:
    prompt = render_context_prompt(_pack())
    for provider in (
        OpenAIProvider(live=False),
        InternalProvider(live=False),
        LocalProvider(live=False),
    ):
        result = await provider.complete(prompt)
        assert result.status.value == "reserved"
        assert result.live is False
        assert result.content is None
        assert result.allow_apply is False


def test_create_app_defaults_to_stub_provider() -> None:
    app = create_app()
    adapter = app.state.model_adapter
    assert isinstance(adapter.provider, StubProvider)
    assert app.state.run_service.agent_mode == "hermes"
    assert not hasattr(app.state.run_service, "_workflow")
    assert not hasattr(app.state, "investigation_agent")
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
    assert created.status_code == 202
    run_id = created.json()["run_id"]
    executed = client.post(f"/api/v1/runs/{run_id}/execute")
    assert executed.status_code == 409
    assert "Hermes" in executed.json()["detail"]


def test_factory_builds_reserved_kinds_without_live() -> None:
    for kind in ("openai", "internal", "local"):
        config = provider_config_from_settings(Settings(model_provider=kind))
        assert config.live is False
        provider = build_model_provider(config)
        assert provider.kind.value == kind
