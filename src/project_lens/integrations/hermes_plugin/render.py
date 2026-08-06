"""Render ProjectLens answer envelopes for Hermes Markdown output."""

from __future__ import annotations

from typing import Any

from project_lens.application.role_views import render_role_view_markdown


def render_answer_envelope(envelope: dict[str, Any], *, audience: str = "team") -> str:
    """Render a ProjectLens ask envelope via RoleView (presentation only).

    Default audience is ``team`` so Feishu first screen stays human-readable.
    Does not invent facts.
    """

    return render_role_view_markdown(envelope, audience=audience)
