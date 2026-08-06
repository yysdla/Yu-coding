"""Operational status endpoint for Feishu deployment checks."""

from __future__ import annotations

from fastapi import APIRouter, Request

from project_lens.config import settings

router = APIRouter()


@router.get("/integrations/feishu/status", tags=["feishu"])
def feishu_status(request: Request) -> dict[str, object]:
    run_service = getattr(request.app.state, "run_service", None)
    ask_run_service = getattr(request.app.state, "ask_run_service", None)
    return {
        "configured": bool(request.app.state.feishu_credentials_configured),
        "outbound_mode": request.app.state.feishu_outbound_mode,
        "binding_count": request.app.state.feishu_binding_count,
        "webhook_path": "/api/v1/feishu/events",
        "agent_mode": getattr(run_service, "agent_mode", settings.agent_mode),
        "ask_agent_mode": getattr(ask_run_service, "agent_mode", None),
        "model_provider": settings.model_provider,
        "model_live": settings.model_live,
        "model_name": settings.model_name,
        "model_openai_model": settings.model_openai_model,
        "model_fallback_to_stub": settings.model_fallback_to_stub,
        # Never expose API keys — only whether a key is configured locally.
        "model_openai_api_key_configured": bool(
            (settings.model_openai_api_key or "").strip()
        ),
    }
