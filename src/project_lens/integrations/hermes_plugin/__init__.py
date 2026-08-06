"""Hermes plugin glue for ProjectLens.

Thin process-boundary adapter: slash commands call the debug/smoke ask API;
formal Agent tools (when Hermes ctx supports register_tool) call
GET/POST /project-agent/tools[/call]. No Kernel imports, no store access,
no Hermes core changes.
"""

from __future__ import annotations

from typing import Any

from project_lens.integrations.hermes_plugin.client import ProjectLensApiClient
from project_lens.integrations.hermes_plugin.commands import ProjectLensCommands
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.hermes_plugin.feishu_client import FeishuCardClient
from project_lens.integrations.hermes_plugin.gateway_hook import (
    pre_gateway_dispatch_card_hook,
)
from project_lens.integrations.hermes_plugin.tools import register_projectlens_tools


def register(ctx: Any) -> None:
    """Register ProjectLens slash commands, optional tools, and Feishu card hook."""

    config = ProjectLensPluginConfig.from_env()
    client = ProjectLensApiClient(config)
    commands = ProjectLensCommands(client=client, config=config)
    card_poster = _maybe_card_poster(config)

    ctx.register_command(
        "project",
        commands.project,
        description=(
            "[DEBUG/SMOKE] Ask ProjectLens via the one-shot ask API. "
            "Formal Agent tool loop should use projectlens_* tools instead."
        ),
        args_hint="<question>",
    )
    ctx.register_command(
        "project-map",
        commands.project_map,
        description="[DEBUG/SMOKE] Ask ProjectLens for a compact architecture/module map.",
        args_hint="",
    )
    ctx.register_command(
        "project-gaps",
        commands.project_gaps,
        description="[DEBUG/SMOKE] Ask ProjectLens what project knowledge is missing or weak.",
        args_hint="",
    )
    ctx.register_command(
        "project-role",
        commands.project_role,
        description="[DEBUG/SMOKE] Ask ProjectLens with a role-specific audience.",
        args_hint="<technical|business|qa|manager|evidence|team> [run_id=<id>] [question]",
    )
    ctx.register_command(
        "card",
        commands.card,
        description="Handle Feishu interactive card RoleView button actions for ProjectLens.",
        args_hint='button {"action":"projectlens_role","audience":"technical","run_id":"..."}',
    )

    # Only when Hermes plugin ctx already exposes register_tool — never patch Hermes core.
    register_projectlens_tools(ctx, client=client, config=config)

    if config.feishu_cards_enabled and card_poster is not None and hasattr(ctx, "register_hook"):

        def _hook(*, event: Any, gateway: Any = None, session_store: Any = None, **_kwargs: Any) -> Any:
            return pre_gateway_dispatch_card_hook(
                event=event,
                commands=commands,
                config=config,
                card_poster=card_poster,
                gateway=gateway,
                session_store=session_store,
            )

        ctx.register_hook("pre_gateway_dispatch", _hook)


def _maybe_card_poster(config: ProjectLensPluginConfig) -> FeishuCardClient | None:
    if not config.feishu_cards_enabled:
        return None
    if not config.feishu_app_id or not config.feishu_app_secret:
        return None
    return FeishuCardClient(
        app_id=config.feishu_app_id,
        app_secret=config.feishu_app_secret,
        base_url=config.feishu_base_url,
        timeout_seconds=min(config.timeout_seconds, 15.0),
    )


__all__ = ["register"]
