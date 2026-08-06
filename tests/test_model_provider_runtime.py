"""Phase 2 provider runtime hardening tests."""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from project_lens.config import Settings
from project_lens.main import create_app
from project_lens.runtime.lifecycle import LifecycleEventType
from project_lens.workflow.context_prompt import render_context_prompt
from project_lens.workflow.model_adapter import ProviderModelAdapter, failed_adapter_result
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.providers import (
    OpenAIProvider,
    ProviderConfigError,
    ProviderKind,
    ProviderRuntimeConfig,
    StubProvider,
    assert_live_disabled,
    create_model_adapter_from_settings,
    provider_config_from_settings,
    validate_provider_config,
)
from tests.test_context_prompt import _pack


def test_default_provider_is_stub() -> None:
    config = provider_config_from_settings(Settings())
    assert config.kind == ProviderKind.STUB
    assert config.live is False
    assert config.fallback_to_stub is True


def test_non_openai_live_requested_is_coerced_offline() -> None:
    """Internal/local/stub cannot go live; OpenAI may request live (needs key to call)."""

    internal = provider_config_from_settings(
        Settings(model_live=True, model_provider="internal")
    )
    assert internal.live is False
    openai = provider_config_from_settings(
        Settings(model_live=True, model_provider="openai")
    )
    assert openai.live is True
    # Without api_key, create still configures live request but provider stays offline.
    adapter = create_model_adapter_from_settings(
        Settings(model_live=True, model_provider="openai", model_openai_api_key=None)
    )
    assert adapter.config.live is True
    assert adapter.live_requested is True


def test_assert_live_disabled_blocks_live_calls() -> None:
    with pytest.raises(RuntimeError, match="refused live call"):
        assert_live_disabled(live=True, provider_name="internal.reserved")
    assert_live_disabled(live=False, provider_name="internal.reserved")


def test_config_validation_rejects_bad_ranges() -> None:
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            ProviderRuntimeConfig.model_construct(timeout_seconds=0.0)
        )
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            ProviderRuntimeConfig.model_construct(max_retries=99)
        )
    with pytest.raises(ProviderConfigError):
        validate_provider_config(
            ProviderRuntimeConfig.model_construct(temperature=9.0)
        )


@pytest.mark.asyncio
async def test_timeout_and_retry_recorded_in_audit() -> None:
    prompt = render_context_prompt(_pack())
    adapter = ProviderModelAdapter(
        StubProvider(simulate_timeouts=1),
        config=ProviderRuntimeConfig(
            kind=ProviderKind.STUB,
            timeout_seconds=0.05,
            max_retries=1,
            max_tokens=512,
            temperature=0.1,
        ),
    )
    result = await adapter.prepare(prompt)
    refs = result.audit_refs()
    assert result.status == "ok"
    assert refs["retries"] == 1
    assert refs["timed_out"] is True
    assert refs["usage"]["total_tokens"] > 0
    assert refs["max_tokens"] == 512
    assert refs["temperature"] == 0.1
    assert refs["live_effective"] is False


@pytest.mark.asyncio
async def test_provider_failure_falls_back_to_stub() -> None:
    prompt = render_context_prompt(_pack())
    adapter = ProviderModelAdapter(
        OpenAIProvider(live=False),
        config=ProviderRuntimeConfig(
            kind=ProviderKind.OPENAI,
            fallback_to_stub=True,
            model_name="gpt-reserved",
        ),
        fallback_provider=StubProvider(model_name="stub-fallback"),
        live_requested=False,
    )
    result = await adapter.prepare(prompt)
    assert result.fallback_used is True
    assert result.status == "fallback"
    assert result.provider == "stub.v1"
    assert result.primary_status == "reserved"
    assert result.audit_refs()["fallback_used"] is True


@pytest.mark.asyncio
async def test_openai_provider_live_false_does_not_network() -> None:
    prompt = render_context_prompt(_pack())
    result = await OpenAIProvider(live=False).complete(prompt)
    assert result.status.value == "reserved"
    assert result.live is False
    assert result.content is None


@pytest.mark.asyncio
async def test_failed_adapter_result_keeps_workflow_safe() -> None:
    prompt = render_context_prompt(_pack())

    class BoomAdapter:
        async def prepare(self, _prompt):
            raise RuntimeError("provider exploded")

    app = create_app()
    app.state.run_service._workflow._model_adapter = BoomAdapter()
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
    assert executed.json()["status"] == "completed"
    result = app.state.run_service._workflow.last_model_adapter_result
    assert result is not None
    assert result.status == "error"
    failed_events = [
        event
        for event in app.state.lifecycle_bus.all()
        if event.type == LifecycleEventType.MODEL_PROVIDER_FAILED
    ]
    assert failed_events
    # Helper shape stays consistent.
    helper = failed_adapter_result(error="x", prompt=prompt)
    assert helper.allow_apply is False


def test_workflow_does_not_import_concrete_providers() -> None:
    source = inspect.getsource(ProjectWorkflow)
    assert "OpenAIProvider" not in source
    assert "InternalProvider" not in source
    assert "LocalProvider" not in source
    assert "StubProvider" not in source
    assert "create_default_model_adapter" in source or "model_adapter" in source


def test_settings_expose_phase2_knobs() -> None:
    settings = Settings(
        model_provider="internal",
        model_max_tokens=1024,
        model_temperature=0.2,
        model_live=True,
        model_fallback_to_stub=True,
    )
    config = provider_config_from_settings(settings)
    assert config.kind == ProviderKind.INTERNAL
    assert config.max_tokens == 1024
    assert config.temperature == 0.2
    # Non-OpenAI live remains coerced off even when MODEL_LIVE=true.
    assert config.live is False
    assert config.fallback_to_stub is True
