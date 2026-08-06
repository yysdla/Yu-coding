"""pre_gateway_dispatch hook: Feishu RoleView cards without Hermes core changes.

Outbound: post interactive card (then patch buttons with message_id) and skip Markdown.
Inbound: /card button → role-view replay → PATCH same message → skip Markdown.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from project_lens.integrations.hermes_plugin.cards import build_role_view_card
from project_lens.integrations.hermes_plugin.commands import (
    DEFAULT_GAPS_QUESTION,
    DEFAULT_MAP_QUESTION,
    ProjectLensCommands,
    SUPPORTED_AUDIENCES,
)
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.hermes_plugin.render import render_answer_envelope

_ASK_SLASH_RE = re.compile(
    r"^(?:@\S+\s+)?/(?P<cmd>project-map|project-gaps|project)(?:\s+(?P<args>.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_CARD_SLASH_RE = re.compile(
    r"^(?:@\S+\s+)?/card(?:\s+(?P<rest>.*))?$",
    re.IGNORECASE | re.DOTALL,
)


class CardPoster(Protocol):
    def post_interactive_card(self, *, chat_id: str, card: dict[str, Any]) -> str:
        """Post a Feishu interactive card; return message_id."""

    def patch_interactive_card(self, *, message_id: str, card: dict[str, Any]) -> None:
        """In-place update an interactive card."""


def pre_gateway_dispatch_card_hook(
    *,
    event: Any,
    commands: ProjectLensCommands,
    config: ProjectLensPluginConfig,
    card_poster: CardPoster | None,
    gateway: Any = None,
    session_store: Any = None,
) -> dict[str, Any] | None:
    """Intercept Feishu ProjectLens asks/card clicks for interactive cards."""

    del gateway, session_store
    if not config.feishu_cards_enabled or card_poster is None:
        return None

    source = getattr(event, "source", None)
    platform = _platform_name(source)
    if platform not in {"feishu", "lark"}:
        return None

    text = str(getattr(event, "text", "") or "").strip()
    card_match = _CARD_SLASH_RE.match(text)
    if card_match is not None:
        return _handle_card_click(
            event=event,
            rest=(card_match.group("rest") or "").strip(),
            commands=commands,
            card_poster=card_poster,
            source=source,
        )

    parsed = _ASK_SLASH_RE.match(text)
    if parsed is None:
        return None
    return _handle_ask_outbound(
        cmd=parsed.group("cmd").lower(),
        args=(parsed.group("args") or "").strip(),
        commands=commands,
        card_poster=card_poster,
        source=source,
    )


def _handle_ask_outbound(
    *,
    cmd: str,
    args: str,
    commands: ProjectLensCommands,
    card_poster: CardPoster,
    source: Any,
) -> dict[str, Any]:
    chat_id = str(getattr(source, "chat_id", "") or "").strip()
    if not chat_id:
        return {"action": "allow"}

    if cmd == "project":
        if not args:
            return {"action": "allow"}
        question, audience = args, "team"
    elif cmd == "project-map":
        question, audience = (args or DEFAULT_MAP_QUESTION), "technical"
    else:
        question, audience = (args or DEFAULT_GAPS_QUESTION), "team"

    _markdown, envelope = commands.ask_result(
        question=question,
        audience=audience,
        chat_id=chat_id,
    )
    run_id = _run_id_from_envelope(envelope)
    if not run_id or envelope.get("ok") is False:
        return {"action": "allow"}

    team_markdown = render_answer_envelope(envelope, audience="team")
    card = build_role_view_card(
        markdown=team_markdown,
        run_id=run_id,
        current_audience="team",
    )
    try:
        message_id = card_poster.post_interactive_card(chat_id=chat_id, card=card)
    except Exception:
        return {"action": "allow"}

    stamped = build_role_view_card(
        markdown=team_markdown,
        run_id=run_id,
        current_audience="team",
        message_id=message_id,
    )
    try:
        card_poster.patch_interactive_card(message_id=message_id, card=stamped)
    except Exception:
        # Card already visible; next click may still recover open_message_id from raw event.
        pass
    return {"action": "skip", "reason": "projectlens_card_sent"}


def _handle_card_click(
    *,
    event: Any,
    rest: str,
    commands: ProjectLensCommands,
    card_poster: CardPoster,
    source: Any,
) -> dict[str, Any]:
    payload = _parse_card_button_payload(rest)
    if payload is None:
        return {"action": "allow"}

    audience = str(payload.get("audience") or "team").strip().lower()
    run_id = str(payload.get("run_id") or "").strip()
    if payload.get("action") != "projectlens_role" or audience not in SUPPORTED_AUDIENCES:
        return {"action": "allow"}
    if not run_id:
        return {"action": "allow"}

    message_id = str(payload.get("message_id") or "").strip()
    if not message_id:
        message_id = _open_message_id_from_event(event)

    if not message_id:
        return {"action": "allow"}

    result = commands.replay_result(run_id=run_id, audience=audience)
    if result.get("ok") is False:
        return {"action": "allow"}

    if isinstance(result.get("markdown"), str) and result["markdown"].strip():
        markdown = str(result["markdown"])
    else:
        markdown = render_answer_envelope(result, audience=audience)

    card = build_role_view_card(
        markdown=markdown,
        run_id=run_id,
        current_audience=audience,
        message_id=message_id,
    )
    try:
        card_poster.patch_interactive_card(message_id=message_id, card=card)
    except Exception:
        return {"action": "allow"}
    return {"action": "skip", "reason": "projectlens_card_patched"}


def _parse_card_button_payload(rest: str) -> dict[str, Any] | None:
    text = rest.strip()
    if text.lower().startswith("button"):
        text = text[6:].strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _open_message_id_from_event(event: Any) -> str:
    raw = getattr(event, "raw_message", None)
    candidates = [
        _dig(raw, "event", "context", "open_message_id"),
        _dig(raw, "context", "open_message_id"),
        getattr(getattr(getattr(raw, "event", None), "context", None), "open_message_id", None),
        getattr(getattr(raw, "context", None), "open_message_id", None),
    ]
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _dig(obj: Any, *keys: str) -> Any:
    current = obj
    for key in keys:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(key)
            continue
        current = getattr(current, key, None)
    return current


def _platform_name(source: Any) -> str:
    platform = getattr(source, "platform", None)
    if platform is None:
        return ""
    value = getattr(platform, "value", platform)
    return str(value).strip().lower()


def _run_id_from_envelope(envelope: dict[str, Any]) -> str | None:
    run_id = envelope.get("run_id")
    if not run_id and isinstance(envelope.get("audit_ref"), dict):
        run_id = envelope["audit_ref"].get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    return None
