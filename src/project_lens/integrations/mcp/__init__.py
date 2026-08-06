"""ProjectLens MCP integration — thin adapter over tool envelope + debug ask."""

from __future__ import annotations

from project_lens.integrations.mcp.handler import (
    FORMAL_TOOL_NAMES,
    ask_project,
    build_ask_service_from_app,
    build_tool_service_from_app,
    call_projectlens_tool,
    list_projectlens_tools,
)

__all__ = [
    "FORMAL_TOOL_NAMES",
    "ask_project",
    "build_ask_service_from_app",
    "build_tool_service_from_app",
    "call_projectlens_tool",
    "list_projectlens_tools",
]
