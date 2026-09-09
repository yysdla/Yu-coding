"""Attach trusted ActorContext during pytest / test_mode runs."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from project_lens.api.trusted_actor import actor_context_from_headers
from project_lens.config import isolation_active


class TrustedActorTestMiddleware(BaseHTTPMiddleware):
    """Populate request.state.actor_context from test headers on project-agent routes."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if isolation_active() and "/project-agent/" in request.url.path:
            existing = getattr(request.state, "actor_context", None)
            if existing is None:
                actor = actor_context_from_headers(request)
                if actor is not None:
                    request.state.actor_context = actor
        return await call_next(request)
