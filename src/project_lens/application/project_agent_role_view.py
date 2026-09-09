"""Replay RoleView Markdown from an existing Project Agent run (no re-investigation)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from project_lens.application.answer_envelope import (
    project_answer_to_envelope,
    recoverable_error,
)
from project_lens.application.audience_views import (
    render_audience_markdown,
    render_audience_view,
)
from project_lens.application.role_views import (
    SUPPORTED_AUDIENCES,
    is_supported_audience,
    normalize_audience,
)
from project_lens.application.run_service import RunService
from project_lens.domain.models import RunStatus
from project_lens.project_space.policies import effective_scope_from_audit_dict


@dataclass(frozen=True)
class RoleViewReplayRequest:
    run_id: UUID
    audience: str = "team"


class ProjectAgentRoleViewService:
    """Re-project an existing run.answer into RoleView Markdown.

    Never calls execute / investigation. Presentation only.
    """

    def __init__(self, *, run_service: RunService) -> None:
        self._run_service = run_service

    def role_view(self, request: RoleViewReplayRequest) -> dict[str, Any]:
        audience_raw = (request.audience or "team").strip().lower()
        if not is_supported_audience(audience_raw):
            allowed = ", ".join(sorted(SUPPORTED_AUDIENCES))
            return recoverable_error(
                error_code="INVALID_AUDIENCE",
                message=f"Unsupported audience '{audience_raw}'. Supported: {allowed}.",
                retryable=True,
                agent_recovery_hint=f"Retry with one of: {allowed}.",
                extras={
                    "run_id": str(request.run_id),
                    "audience": audience_raw,
                    "http_status": 400,
                },
            )

        audience = normalize_audience(audience_raw)
        run = self._run_service.get(request.run_id)
        if run is None:
            return recoverable_error(
                error_code="RUN_NOT_FOUND",
                message=f"run {request.run_id} was not found",
                retryable=False,
                agent_recovery_hint=(
                    "Ask again with /project <question>, then switch RoleView "
                    "with /project-role <audience>."
                ),
                extras={
                    "run_id": str(request.run_id),
                    "audience": audience,
                    "http_status": 404,
                },
            )

        if run.answer is None or run.status != RunStatus.COMPLETED:
            return recoverable_error(
                error_code="RUN_NOT_READY",
                message=(
                    f"run {request.run_id} has no completed answer "
                    f"(status={run.status.value})."
                ),
                retryable=True,
                agent_recovery_hint="Wait for the investigation to finish, or ask again.",
                project=run.project,
                extras={
                    "run_id": str(run.id),
                    "trace_id": str(run.trace_id),
                    "audience": audience,
                    "http_status": 409,
                },
            )

        events = self._run_service.events(run.id)
        tool_names: tuple[str, ...] = ()
        investigation = getattr(self._run_service, "_investigation", None)
        if investigation is not None:
            tool_names = tuple(getattr(investigation, "last_tool_names", ()) or ())

        envelope = project_answer_to_envelope(
            run=run,
            answer=run.answer,
            events=events,
            tool_names=tool_names or None,
            audience=audience,
            format="concise",
        )
        if run.runtime_access is None or not run.channel_id:
            return recoverable_error(
                error_code="ACCESS_SCOPE_MISSING",
                message="run has no effective access scope for audience replay",
                retryable=False,
                agent_recovery_hint="Ask the project question again with a trusted identity.",
                project=run.project,
                extras={"run_id": str(run.id), "http_status": 409},
            )
        scope = effective_scope_from_audit_dict(
            run.runtime_access,
            project=run.project,
            actor_id=run.user_id,
            chat_id=run.channel_id,
        )
        view = render_audience_view(
            run.answer,
            role=scope.role,
            chat_type=scope.chat_type,
            scope=scope,
            audience=None if audience == "team" else audience,
        )
        markdown = render_audience_markdown(view)
        return {
            "ok": True,
            "audience": audience,
            "run_id": str(run.id),
            "trace_id": str(run.trace_id),
            "markdown": markdown,
            "audience_view": view.to_dict(),
            "envelope_ref": {
                "answer_summary": envelope.get("answer_summary"),
                "fact_count": len(envelope.get("facts") or []),
                "citation_count": len(envelope.get("citations") or []),
                "unknown_count": len(envelope.get("unknowns") or []),
            },
            "audit_ref": envelope.get("audit_ref")
            if isinstance(envelope.get("audit_ref"), dict)
            else {"allow_apply": False},
            "role_views_available": envelope.get("role_views_available") or [],
        }
