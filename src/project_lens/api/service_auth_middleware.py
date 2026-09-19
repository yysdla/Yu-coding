"""Minimal production service-token gate for ProjectLens internal APIs."""
from __future__ import annotations
import secrets
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from project_lens.config import settings
from project_lens.api.trusted_actor import actor_context_from_headers

class ServiceAuthMiddleware(BaseHTTPMiddleware):
    """Require a configured bearer token on internal project-agent/Hermes APIs."""
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        prefix = settings.api_prefix.rstrip("/")
        protected = (
            request.url.path.startswith(f"{prefix}/project-agent")
            or request.url.path.startswith(f"{prefix}/hermes")
            or request.url.path.startswith(f"{prefix}/release-gate")
            or request.url.path.startswith(f"{prefix}/memory-proposals")
            or request.url.path.startswith(f"{prefix}/projects/memory-proposals")
            or (request.url.path.startswith(f"{prefix}/projects/") and "/memories" in request.url.path)
        )
        if protected and settings.env.strip().lower() in {"production", "pilot"}:
            configured = (settings.service_token or "").strip()
            supplied = request.headers.get("Authorization", "")
            token = supplied[7:].strip() if supplied.lower().startswith("bearer ") else ""
            if not configured or not token or not secrets.compare_digest(configured, token):
                return JSONResponse({"detail": "missing or invalid service authentication"}, status_code=401)
            actor = actor_context_from_headers(request)
            if actor is not None:
                request.state.actor_context = actor
        return await call_next(request)
