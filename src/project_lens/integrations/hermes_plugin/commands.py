"""Hermes slash command handlers for ProjectLens."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.hermes_plugin.render import render_answer_envelope

DEFAULT_MAP_QUESTION = "Please introduce this project's architecture, entrypoints, and main modules."
DEFAULT_GAPS_QUESTION = "What important project knowledge is missing or weak right now?"

SUPPORTED_AUDIENCES = frozenset(
    {
        "team",
        "technical",
        "business",
        "qa",
        "manager",
        "evidence",
        "onboarding",
        "debug",
        "map",
    }
)

_RUN_ID_RE = re.compile(r"(?:^|\s)run_id=([^\s]+)", re.IGNORECASE)

# Process-local cache for Phase 4 pilot (single-user Feishu). Not Hermes memory.
_last_run_id: str | None = None


class AskClient(Protocol):
    def ask_project(self, request: dict[str, Any]) -> dict[str, Any]:
        """Call ProjectLens ask API and return its answer envelope."""

    def role_view(self, run_id: str, audience: str) -> dict[str, Any]:
        """Replay RoleView Markdown for an existing run (no re-investigation)."""


class ProjectLensCommands:
    """Small command layer between Hermes and ProjectLens ask / role-view APIs."""

    def __init__(self, *, client: AskClient, config: ProjectLensPluginConfig) -> None:
        self._client = client
        self._config = config

    def project(self, raw_args: str) -> str:
        question = raw_args.strip()
        if not question:
            return "Usage: /project <project question>"
        return self._ask(question=question, audience="team")

    def project_map(self, raw_args: str) -> str:
        question = raw_args.strip() or DEFAULT_MAP_QUESTION
        return self._ask(question=question, audience="technical")

    def project_gaps(self, raw_args: str) -> str:
        question = raw_args.strip() or DEFAULT_GAPS_QUESTION
        return self._ask(question=question, audience="team")

    def project_role(self, raw_args: str) -> str:
        raw = raw_args.strip()
        if not raw:
            return (
                "Usage: /project-role <technical|business|qa|manager|evidence|team> "
                "[run_id=<id>] [question]"
            )
        audience, _, rest = raw.partition(" ")
        audience = audience.strip().lower()
        if audience not in SUPPORTED_AUDIENCES:
            allowed = ", ".join(sorted(SUPPORTED_AUDIENCES))
            return f"Unknown audience: {audience}. Supported audiences: {allowed}."

        run_id, question = _extract_run_id(rest.strip())
        if not run_id:
            run_id = _last_run_id

        if run_id:
            return self._replay(run_id=run_id, audience=audience)

        if not question:
            question = f"Please summarize the current project for the {audience} audience."
        return self._ask(question=question, audience=audience)

    def card(self, raw_args: str) -> str:
        """Handle Hermes synthetic `/card button {json}` RoleView actions."""

        rest = raw_args.strip()
        lowered = rest.lower()
        if lowered.startswith("button"):
            rest = rest[6:].strip()
        if not rest:
            return "Usage: /card button {\"action\":\"projectlens_role\",\"audience\":\"technical\",\"run_id\":\"...\"}"
        return self.card_button(rest)

    def card_button(self, raw_args: str) -> str:
        """Replay RoleView for a card action when run_id + audience are present."""

        try:
            payload = json.loads(raw_args)
        except json.JSONDecodeError:
            return "Unsupported card payload. Use /project-role <audience> <question>."
        if not isinstance(payload, dict):
            return "Unsupported card payload. Use /project-role <audience> <question>."
        audience = str(payload.get("audience") or "team").lower()
        run_id = str(payload.get("run_id") or "").strip()
        if payload.get("action") != "projectlens_role" or audience not in SUPPORTED_AUDIENCES:
            return "Unsupported ProjectLens card action. Use /project-role <audience> <question>."
        if run_id:
            return self._replay(run_id=run_id, audience=audience)
        if _last_run_id:
            return self._replay(run_id=_last_run_id, audience=audience)
        return (
            f"No run_id available for RoleView replay. "
            f"Use /project-role {audience} <question> to ask first."
        )

    def ask_result(
        self,
        *,
        question: str,
        audience: str,
        chat_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Ask ProjectLens and return (markdown, envelope) for card / hook paths."""

        request = {
            "question": question,
            "project": self._config.project_for_chat(chat_id),
            "user_id": self._config.default_user_id,
            "channel_id": chat_id,
            "audience": audience,
            "mode": "read_only",
            "format": "concise",
        }
        envelope = self._client.ask_project(request)
        self._remember_run_id(envelope)
        return render_answer_envelope(envelope, audience=audience), envelope

    def _ask(self, *, question: str, audience: str, chat_id: str | None = None) -> str:
        markdown, _envelope = self.ask_result(
            question=question,
            audience=audience,
            chat_id=chat_id,
        )
        return markdown

    def _replay(self, *, run_id: str, audience: str) -> str:
        result = self.replay_result(run_id=run_id, audience=audience)
        if result.get("ok") and isinstance(result.get("markdown"), str):
            return str(result["markdown"])
        # Fall back to RoleView error rendering from envelope fields.
        return render_answer_envelope(result, audience=audience)

    def replay_result(self, *, run_id: str, audience: str) -> dict[str, Any]:
        """Replay RoleView for a run; returns API payload (presentation only)."""

        result = self._client.role_view(run_id, audience)
        self._remember_run_id(result)
        return result

    def _remember_run_id(self, envelope: dict[str, Any]) -> None:
        global _last_run_id
        run_id = envelope.get("run_id")
        if not run_id and isinstance(envelope.get("audit_ref"), dict):
            run_id = envelope["audit_ref"].get("run_id")
        if isinstance(run_id, str) and run_id.strip():
            _last_run_id = run_id.strip()


def _extract_run_id(rest: str) -> tuple[str | None, str]:
    if not rest:
        return None, ""
    match = _RUN_ID_RE.search(rest)
    if not match:
        return None, rest
    run_id = match.group(1).strip()
    question = (_RUN_ID_RE.sub(" ", rest)).strip()
    question = " ".join(question.split())
    return run_id or None, question


def reset_last_run_id_for_tests() -> None:
    """Test helper: clear process-local run cache."""

    global _last_run_id
    _last_run_id = None
