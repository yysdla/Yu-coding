"""Build ModelProvider / ModelAdapter from settings without workflow coupling."""

from __future__ import annotations

from typing import Any

from project_lens.workflow.providers.base import (
    ModelProvider,
    ProviderKind,
    ProviderRuntimeConfig,
    validate_provider_config,
)
from project_lens.workflow.providers.internal import InternalProvider
from project_lens.workflow.providers.local import LocalProvider
from project_lens.workflow.providers.openai_provider import OpenAIProvider
from project_lens.workflow.providers.stub import StubProvider
from project_lens.workflow.providers.transport import ChatTransport


def provider_config_from_settings(settings: Any) -> ProviderRuntimeConfig:
    kind_raw = str(getattr(settings, "model_provider", "stub") or "stub").strip().lower()
    try:
        kind = ProviderKind(kind_raw)
    except ValueError as exc:
        raise ValueError(
            f"unknown model_provider={kind_raw!r}; "
            f"expected one of {[item.value for item in ProviderKind]}"
        ) from exc
    live_requested = _as_bool(getattr(settings, "model_live", False), default=False)
    fallback = _as_bool(getattr(settings, "model_fallback_to_stub", True), default=True)
    config = ProviderRuntimeConfig(
        kind=kind,
        model_name=str(getattr(settings, "model_name", "stub-v1") or "stub-v1"),
        timeout_seconds=float(getattr(settings, "model_timeout_seconds", 30.0) or 30.0),
        max_retries=_as_int(getattr(settings, "model_max_retries", 1), default=1),
        max_tokens=_as_int(getattr(settings, "model_max_tokens", 2048), default=2048),
        temperature=float(getattr(settings, "model_temperature", 0.0) or 0.0),
        live=live_requested,
        fallback_to_stub=fallback,
        openai_base_url=getattr(settings, "model_openai_base_url", None),
        openai_model=getattr(settings, "model_openai_model", None),
        internal_endpoint=getattr(settings, "model_internal_endpoint", None),
        local_model_path=getattr(settings, "model_local_path", None),
    )
    return validate_provider_config(config)


def build_model_provider(
    config: ProviderRuntimeConfig,
    *,
    api_key: str | None = None,
    transport: ChatTransport | None = None,
) -> ModelProvider:
    if config.kind == ProviderKind.STUB:
        return StubProvider(model_name=config.model_name)
    if config.kind == ProviderKind.OPENAI:
        return OpenAIProvider(
            model_name=config.openai_model or config.model_name,
            base_url=config.openai_base_url,
            live=config.live,
            api_key=api_key,
            transport=transport,
            timeout_seconds=config.timeout_seconds,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
    if config.kind == ProviderKind.INTERNAL:
        return InternalProvider(
            model_name=config.model_name,
            endpoint=config.internal_endpoint,
            live=False,
        )
    if config.kind == ProviderKind.LOCAL:
        return LocalProvider(
            model_name=config.model_name,
            model_path=config.local_model_path,
            live=False,
        )
    raise ValueError(f"unsupported provider kind: {config.kind}")


def create_default_model_adapter():
    """Stub-backed adapter for unit tests and safe defaults."""

    from project_lens.workflow.model_adapter import ProviderModelAdapter

    config = validate_provider_config(ProviderRuntimeConfig())
    return ProviderModelAdapter(
        StubProvider(model_name=config.model_name),
        config=config,
        fallback_provider=None,
        live_requested=False,
    )


def create_model_adapter_from_settings(
    settings: Any,
    *,
    transport: ChatTransport | None = None,
):
    from project_lens.workflow.model_adapter import ProviderModelAdapter

    live_requested = _as_bool(getattr(settings, "model_live", False), default=False)
    config = provider_config_from_settings(settings)
    api_key = getattr(settings, "model_openai_api_key", None)
    primary = build_model_provider(config, api_key=api_key, transport=transport)
    fallback = None
    if config.fallback_to_stub and config.kind != ProviderKind.STUB:
        fallback = StubProvider(model_name="stub-fallback")
    return ProviderModelAdapter(
        primary,
        config=config,
        fallback_provider=fallback,
        live_requested=live_requested,
    )


def _as_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default
