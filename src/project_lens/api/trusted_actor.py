"""Trusted actor resolution for Project Agent HTTP routes."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from project_lens.config import isolation_active, settings
from project_lens.domain.identity import ActorContext, ChatType


def get_trusted_actor_context(request: Request) -> ActorContext:
    """Return the trusted actor bound at ingress; never trust client body directly."""

    actor = getattr(request.state, "actor_context", None)
    if isinstance(actor, ActorContext):
        return actor
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing trusted actor context",
    )


def resolve_project_agent_actor(
    request: Request,
    *,
    body_user_id: str,
    body_channel_id: str | None,
    project_tenant_id: str,
) -> ActorContext:
    """Resolve actor for project-agent routes under Phase 1 trust rules."""

    if isolation_active() or settings.env.strip().lower() in {"test", "production"}:
        return get_trusted_actor_context(request)

    chat_id = (body_channel_id or "api-local").strip()
    return ActorContext(
        tenant_key=project_tenant_id,
        actor_id=body_user_id.strip(),
        chat_id=chat_id,
        chat_type=_infer_chat_type(chat_id),
        source="test_fixture",
        authenticated=True,
    )


def actor_context_from_headers(request: Request) -> ActorContext | None:
    """Test harness: build ActorContext from X-ProjectLens-* headers."""

    actor_id = request.headers.get("X-ProjectLens-Actor-Id", "").strip()
    chat_id = request.headers.get("X-ProjectLens-Chat-Id", "").strip()
    tenant_key = request.headers.get("X-ProjectLens-Tenant-Key", "").strip()
    chat_type = request.headers.get("X-ProjectLens-Chat-Type", "group").strip().lower()
    if not actor_id or not chat_id or not tenant_key:
        return None
    if chat_type not in {"p2p", "group"}:
        chat_type = "group"
    return ActorContext(
        tenant_key=tenant_key,
        actor_id=actor_id,
        chat_id=chat_id,
        chat_type=chat_type,  # type: ignore[arg-type]
        source="test_fixture",
        authenticated=True,
    )


def _infer_chat_type(chat_id: str) -> ChatType:
    if chat_id.startswith("oc_") or chat_id.startswith("chat-"):
        return "group"
    return "p2p"
