"""ModelAdapter: ContextPrompt -> Provider with timeout/retry/fallback/usage audit.

Workflow depends only on this adapter protocol, never on OpenAI/Internal/Local
concrete classes. All model input must be a ContextPrompt.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from project_lens.workflow.context_prompt import ContextPrompt
from project_lens.workflow.providers.base import (
    ModelProvider,
    ProviderAttempt,
    ProviderKind,
    ProviderResult,
    ProviderRuntimeConfig,
    ProviderStatus,
    assert_prompt_safe,
)
from project_lens.workflow.providers.stub import StubProvider


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelAdapterResult(FrozenModel):
    """Observation from preparing a model call. Not a ProjectAnswer."""

    adapter: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=100)
    provider_kind: str = Field(min_length=1, max_length=50)
    model_name: str = Field(default="unknown", max_length=200)
    used_prompt: bool = True
    section_layers: tuple[str, ...] = ()
    message_count: int = Field(ge=0)
    allow_apply: bool = False
    status: str = ProviderStatus.OK.value
    usage: dict[str, int] = Field(default_factory=dict)
    retries: int = Field(default=0, ge=0)
    timed_out: bool = False
    attempts: tuple[ProviderAttempt, ...] = ()
    notes: tuple[str, ...] = ()
    live: bool = False
    live_requested: bool = False
    live_effective: bool = False
    fallback_used: bool = False
    primary_status: str | None = None
    max_tokens: int = 2048
    temperature: float = 0.0
    content: str | None = None

    def audit_refs(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "provider": self.provider,
            "provider_kind": self.provider_kind,
            "model_name": self.model_name,
            "used_prompt": self.used_prompt,
            "section_layers": list(self.section_layers),
            "message_count": self.message_count,
            "allow_apply": False,
            "status": self.status,
            "usage": dict(self.usage),
            "has_content": self.content is not None,
            "content_chars": len(self.content or ""),
            "retries": self.retries,
            "timed_out": self.timed_out,
            "attempt_count": len(self.attempts),
            "attempts": [
                {
                    "attempt": item.attempt,
                    "ok": item.ok,
                    "timed_out": item.timed_out,
                    "error": item.error,
                    "latency_ms": item.latency_ms,
                }
                for item in self.attempts
            ],
            "notes": list(self.notes),
            "live": self.live_effective,
            "live_requested": self.live_requested,
            "live_effective": self.live_effective,
            "fallback_used": self.fallback_used,
            "primary_status": self.primary_status,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            # Never include full LLM content in audit refs.
        }


class ModelAdapter(Protocol):
    async def prepare(self, prompt: ContextPrompt) -> ModelAdapterResult:
        """Accept only a rendered ContextPrompt. Must not fetch EvidenceIndex."""
        ...


class ProviderModelAdapter:
    """Runs a ModelProvider with timeout / retry / stub fallback and usage audit."""

    name = "model_adapter.v1"

    def __init__(
        self,
        provider: ModelProvider,
        *,
        config: ProviderRuntimeConfig | None = None,
        fallback_provider: ModelProvider | None = None,
        live_requested: bool = False,
    ) -> None:
        self._provider = provider
        self._config = config or ProviderRuntimeConfig(
            kind=getattr(provider, "kind", ProviderKind.STUB)
        )
        self._fallback = fallback_provider
        self._live_requested = live_requested

    @property
    def provider(self) -> ModelProvider:
        return self._provider

    @property
    def config(self) -> ProviderRuntimeConfig:
        return self._config

    @property
    def live_requested(self) -> bool:
        return self._live_requested

    async def prepare(self, prompt: ContextPrompt) -> ModelAdapterResult:
        layers = assert_prompt_safe(prompt)
        primary_result, attempts, timed_out_any, last_error = await self._run_provider(
            self._provider,
            prompt,
            max_tries=self._config.max_retries + 1,
        )
        retries = max(0, len(attempts) - 1)
        model_name = self._config.model_name or "unknown"
        primary_status = (
            primary_result.status.value if primary_result is not None else None
        )

        if primary_result is not None and primary_result.status == ProviderStatus.OK:
            return self._success_result(
                prompt=prompt,
                layers=layers,
                provider_result=primary_result,
                attempts=attempts,
                retries=retries,
                timed_out_any=timed_out_any,
                model_name=model_name,
                fallback_used=False,
                primary_status=primary_status,
            )

        # Fallback when primary timed out / errored / reserved (non-ok).
        if self._fallback is not None and self._config.fallback_to_stub:
            fallback_result, fb_attempts, fb_timeout, fb_error = await self._run_provider(
                self._fallback,
                prompt,
                max_tries=1,
                attempt_offset=len(attempts),
            )
            attempts = attempts + fb_attempts
            if fallback_result is not None and fallback_result.status == ProviderStatus.OK:
                return self._success_result(
                    prompt=prompt,
                    layers=layers,
                    provider_result=fallback_result,
                    attempts=attempts,
                    retries=retries,
                    timed_out_any=timed_out_any or fb_timeout,
                    model_name=model_name,
                    fallback_used=True,
                    primary_status=primary_status
                    or (
                        ProviderStatus.TIMEOUT.value
                        if timed_out_any
                        else ProviderStatus.ERROR.value
                    ),
                    extra_notes=(
                        "fallback_to_stub=true",
                        f"primary_error={last_error or primary_status or 'unknown'}",
                        f"fallback_error={fb_error or ''}",
                    ),
                    status_override=ProviderStatus.FALLBACK,
                )

        kind = getattr(self._provider, "kind", self._config.kind)
        kind_value = kind.value if isinstance(kind, ProviderKind) else str(kind)
        status = (
            ProviderStatus.TIMEOUT.value
            if timed_out_any
            else (primary_status or ProviderStatus.ERROR.value)
        )
        return ModelAdapterResult(
            adapter=self.name,
            provider=getattr(self._provider, "name", "unknown"),
            provider_kind=kind_value,
            model_name=model_name,
            used_prompt=True,
            section_layers=layers,
            message_count=len(prompt.messages),
            allow_apply=False,
            status=status,
            usage={},
            retries=retries,
            timed_out=timed_out_any,
            attempts=tuple(attempts),
            notes=(
                "provider failed after retries",
                f"last_error={last_error or 'unknown'}",
                f"model_name={model_name}",
                f"live_requested={self._live_requested}",
                "live_effective=False",
            ),
            live=False,
            live_requested=self._live_requested,
            live_effective=False,
            fallback_used=False,
            primary_status=primary_status or status,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
        )

    async def _run_provider(
        self,
        provider: ModelProvider,
        prompt: ContextPrompt,
        *,
        max_tries: int,
        attempt_offset: int = 0,
    ) -> tuple[ProviderResult | None, list[ProviderAttempt], bool, str | None]:
        attempts: list[ProviderAttempt] = []
        timed_out_any = False
        last_error: str | None = None
        provider_result: ProviderResult | None = None
        for index in range(max_tries):
            attempt_no = attempt_offset + index + 1
            started = time.perf_counter()
            try:
                provider_result = await asyncio.wait_for(
                    provider.complete(prompt),
                    timeout=self._config.timeout_seconds,
                )
                if provider_result.allow_apply:
                    raise ValueError("provider result allow_apply=True refused")
                latency_ms = (time.perf_counter() - started) * 1000.0
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_no,
                        ok=provider_result.status == ProviderStatus.OK,
                        timed_out=False,
                        error=(
                            None
                            if provider_result.status == ProviderStatus.OK
                            else provider_result.status.value
                        ),
                        latency_ms=round(latency_ms, 3),
                    )
                )
                if provider_result.status == ProviderStatus.OK:
                    break
                # Non-ok (reserved/error) stops retry loop; caller may fallback.
                last_error = provider_result.status.value
                break
            except TimeoutError:
                timed_out_any = True
                last_error = "timeout"
                latency_ms = (time.perf_counter() - started) * 1000.0
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_no,
                        ok=False,
                        timed_out=True,
                        error="timeout",
                        latency_ms=round(latency_ms, 3),
                    )
                )
                provider_result = None
            except Exception as exc:  # noqa: BLE001 - record then retry
                last_error = str(exc)
                latency_ms = (time.perf_counter() - started) * 1000.0
                attempts.append(
                    ProviderAttempt(
                        attempt=attempt_no,
                        ok=False,
                        timed_out=False,
                        error=last_error[:500],
                        latency_ms=round(latency_ms, 3),
                    )
                )
                provider_result = None
        return provider_result, attempts, timed_out_any, last_error

    def _success_result(
        self,
        *,
        prompt: ContextPrompt,
        layers: tuple[str, ...],
        provider_result: ProviderResult,
        attempts: list[ProviderAttempt],
        retries: int,
        timed_out_any: bool,
        model_name: str,
        fallback_used: bool,
        primary_status: str | None,
        extra_notes: tuple[str, ...] = (),
        status_override: ProviderStatus | None = None,
    ) -> ModelAdapterResult:
        kind_value = (
            provider_result.kind.value
            if isinstance(provider_result.kind, ProviderKind)
            else str(provider_result.kind)
        )
        usage = provider_result.usage.as_dict()
        capped_note: tuple[str, ...] = ()
        # Soft-cap reported usage for audit accounting (does not truncate prompt).
        if usage.get("total_tokens", 0) > self._config.max_tokens:
            usage = {**usage, "total_tokens": self._config.max_tokens}
            capped_note = ("usage_total_tokens_capped=true",)
        status = (status_override or provider_result.status).value
        live_effective = bool(provider_result.live)
        notes = provider_result.notes + (
            f"model_name={model_name}",
            f"live_requested={self._live_requested}",
            f"live_effective={live_effective}",
            f"max_tokens={self._config.max_tokens}",
            f"temperature={self._config.temperature}",
        ) + capped_note + extra_notes
        return ModelAdapterResult(
            adapter=self.name,
            provider=provider_result.provider,
            provider_kind=kind_value,
            model_name=model_name,
            used_prompt=True,
            section_layers=layers,
            message_count=len(prompt.messages),
            allow_apply=False,
            status=status,
            usage=usage,
            retries=retries,
            timed_out=timed_out_any,
            attempts=tuple(attempts),
            notes=notes,
            live=live_effective,
            live_requested=self._live_requested,
            live_effective=live_effective,
            fallback_used=fallback_used,
            primary_status=primary_status,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            content=provider_result.content,
        )


class StubModelAdapter(ProviderModelAdapter):
    """Backward-compatible default: StubProvider behind ProviderModelAdapter."""

    def __init__(self) -> None:
        super().__init__(StubProvider(), config=ProviderRuntimeConfig())


def failed_adapter_result(
    *,
    error: str,
    prompt: ContextPrompt | None = None,
    live_requested: bool = False,
) -> ModelAdapterResult:
    """Safe result when adapter.prepare raises — keeps Feishu/workflow alive."""

    layers = (
        tuple(item.layer for item in prompt.sections)
        if prompt is not None
        else ()
    )
    return ModelAdapterResult(
        adapter="model_adapter.v1",
        provider="unavailable",
        provider_kind="stub",
        model_name="unavailable",
        used_prompt=prompt is not None,
        section_layers=layers,
        message_count=len(prompt.messages) if prompt is not None else 0,
        allow_apply=False,
        status=ProviderStatus.ERROR.value,
        usage={},
        retries=0,
        timed_out=False,
        attempts=(),
        notes=("adapter prepare failed", f"error={error[:500]}", "live_effective=False"),
        live=False,
        live_requested=live_requested,
        live_effective=False,
        fallback_used=False,
        primary_status=ProviderStatus.ERROR.value,
    )
