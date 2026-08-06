"""Choose investigation chat provider: stub planner or live OpenAI tool loop."""

from __future__ import annotations

from typing import Any

from project_lens.agent.openai_loop_provider import OpenAILoopProvider
from project_lens.agent.stub_planner import InvestigationStubProvider
from project_lens.config import Settings, settings
from project_lens.runtime.types import ModelProvider
from project_lens.workflow.providers.transport import ChatTransport


def create_investigation_provider(
    question: str,
    *,
    app_settings: Settings | None = None,
    transport: ChatTransport | None = None,
    prior_paths: tuple[str, ...] = (),
    prior_hints: tuple[str, ...] = (),
) -> tuple[ModelProvider, dict[str, Any]]:
    """Return (provider, audit_meta). Live only when model_live + api_key."""

    cfg = app_settings or settings
    provider_name = (cfg.model_provider or "stub").strip().lower()
    live = bool(cfg.model_live)
    api_key = (cfg.model_openai_api_key or "").strip()
    meta: dict[str, Any] = {
        "investigation_provider": "stub_planner",
        "model_provider": provider_name,
        "model_live": live,
        "live_effective": False,
        "allow_apply": False,
        "prior_path_count": len(prior_paths),
        "prior_hint_count": len(prior_hints),
    }
    if live and provider_name in {"openai", "openai.v1"} and api_key:
        model_name = cfg.model_openai_model or cfg.model_name or "gpt-4o-mini"
        loop_provider = OpenAILoopProvider(
            model_name=model_name,
            api_key=api_key,
            base_url=cfg.model_openai_base_url,
            transport=transport,
            timeout_seconds=float(cfg.model_timeout_seconds),
            max_tokens=int(cfg.model_max_tokens),
            temperature=float(cfg.model_temperature),
        )
        meta.update(
            {
                "investigation_provider": "openai_loop",
                "model_name": model_name,
                "live_effective": True,
            }
        )
        return loop_provider, meta
    return (
        InvestigationStubProvider(
            question=question,
            prior_paths=prior_paths,
            prior_hints=prior_hints,
        ),
        meta,
    )
