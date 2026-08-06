"""Feishu interactive RoleView cards for the Hermes ProjectLens plugin.

Presentation only: wraps already-rendered RoleView Markdown and attaches
role-switch buttons that Hermes turns into `/card button {json}`.
Does not invent project facts.
"""

from __future__ import annotations

from typing import Any

# Buttons shown on the default outbound card (cap Feishu action row).
_DEFAULT_ROLE_BUTTONS: tuple[tuple[str, str], ...] = (
    ("技术视图", "technical"),
    ("查看证据", "evidence"),
    ("项目地图", "map"),
)

_AUDIENCE_CARD_TITLES: dict[str, str] = {
    "team": "ProjectLens · 团队视图",
    "technical": "ProjectLens · 技术视图",
    "evidence": "ProjectLens · 证据视图",
    "map": "ProjectLens · 项目地图",
    "business": "ProjectLens · 业务视图",
}


def build_role_view_card(
    *,
    markdown: str,
    run_id: str,
    title: str | None = None,
    current_audience: str = "team",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Build a Feishu interactive card for a ProjectLens RoleView reply."""

    body = (markdown or "").strip() or "当前没有可展示的结论。"
    audience = (current_audience or "team").strip().lower()
    header_title = (title or _AUDIENCE_CARD_TITLES.get(audience) or "ProjectLens")[:40]
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": body},
        _role_action_row(
            run_id=run_id,
            current_audience=audience,
            message_id=message_id,
        ),
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": (
                        f"run_id={run_id} · allow_apply=false · "
                        "切换视图原地更新，不重新调查"
                    ),
                }
            ],
        },
    ]
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title or "ProjectLens"},
            "template": "blue",
        },
        "elements": elements,
    }


def role_button_value(
    *,
    audience: str,
    run_id: str,
    message_id: str | None = None,
) -> dict[str, str]:
    """Button value Hermes synthesizes into `/card button {...}`."""

    payload = {
        "action": "projectlens_role",
        "audience": audience,
        "run_id": run_id,
    }
    if message_id and message_id.strip():
        payload["message_id"] = message_id.strip()
    return payload


def _role_action_row(
    *,
    run_id: str,
    current_audience: str,
    message_id: str | None = None,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for label, audience in _DEFAULT_ROLE_BUTTONS:
        if audience == current_audience:
            continue
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": label},
                "type": "primary" if audience == "technical" else "default",
                "value": role_button_value(
                    audience=audience,
                    run_id=run_id,
                    message_id=message_id,
                ),
            }
        )
    if current_audience != "team" and len(actions) < 4:
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "团队视图"},
                "type": "default",
                "value": role_button_value(
                    audience="team",
                    run_id=run_id,
                    message_id=message_id,
                ),
            }
        )
    return {"tag": "action", "actions": actions[:4]}
