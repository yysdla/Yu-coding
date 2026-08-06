"""ProjectLens MCP server — formal projectlens_* tools + debug ask.

Requires optional dependency: pip install -e ".[mcp]"
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from project_lens.integrations.mcp.handler import (
    FORMAL_TOOL_NAMES,
    TOOL_DESCRIPTION,
    TOOL_NAME,
    ask_project,
    build_ask_service_from_app,
    build_tool_service_from_app,
    call_projectlens_tool,
    list_projectlens_tools,
)


def _require_mcp() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised when extra missing
        raise SystemExit(
            "ProjectLens MCP server requires the optional 'mcp' extra.\n"
            'Install with: pip install -e ".[mcp]"\n'
            f"Import error: {exc}"
        ) from exc
    return FastMCP


def create_mcp_server(
    ask_service: Any | None = None,
    tool_service: Any | None = None,
) -> Any:
    """Build a FastMCP server bound to ProjectAgent tool + ask services."""

    FastMCP = _require_mcp()
    if ask_service is None or tool_service is None:
        from project_lens.main import create_app

        app = create_app()
        if ask_service is None:
            ask_service = build_ask_service_from_app(app)
        if tool_service is None:
            tool_service = build_tool_service_from_app(app)

    catalog = list_projectlens_tools(tool_service)
    tools = catalog.get("tools") if isinstance(catalog, dict) else None
    if not isinstance(tools, list):
        raise RuntimeError("ProjectAgentToolService.list_tools() returned invalid catalog")

    formal_names = {item["name"] for item in tools if isinstance(item, dict) and "name" in item}
    if formal_names != set(FORMAL_TOOL_NAMES):
        raise RuntimeError(
            f"MCP formal tool catalog mismatch: got {sorted(formal_names)}, "
            f"expected {sorted(FORMAL_TOOL_NAMES)}"
        )
    for item in tools:
        if item.get("allow_apply") is not False:
            raise RuntimeError(f"MCP tool {item.get('name')} must keep allow_apply=false")

    mcp = FastMCP("projectlens")

    for spec in tools:
        _register_formal_tool(mcp, tool_service=tool_service, spec=spec)

    @mcp.tool(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
    )
    async def projectlens_ask_project(
        question: str,
        project_id: str,
        tenant_id: str = "demo",
        user_id: str = "mcp-user",
        channel_id: str | None = None,
        audience: str = "team",
        format: str = "concise",
    ) -> str:
        result = await ask_project(
            ask_service,
            question=question,
            project_id=project_id,
            tenant_id=tenant_id,
            user_id=user_id,
            channel_id=channel_id,
            audience=audience,
            format=format,
        )
        return json.dumps(result, ensure_ascii=False)

    return mcp


def _register_formal_tool(mcp: Any, *, tool_service: Any, spec: dict[str, Any]) -> None:
    tool_name = str(spec["name"])
    description = str(spec.get("description") or tool_name)

    async def _handler(
        project_id: str,
        tenant_id: str = "demo",
        user_id: str = "mcp-user",
        chat_id: str = "mcp-chat",
        arguments: dict[str, Any] | None = None,
    ) -> str:
        result = await call_projectlens_tool(
            tool_service,
            tool_name=tool_name,
            project_id=project_id,
            tenant_id=tenant_id,
            user_id=user_id,
            chat_id=chat_id,
            arguments=arguments or {},
        )
        return json.dumps(result, ensure_ascii=False)

    _handler.__name__ = tool_name
    _handler.__qualname__ = tool_name
    decorator: Callable[..., Any] = mcp.tool(name=tool_name, description=description)
    decorator(_handler)


def main() -> None:
    # Keep stdout reserved for MCP JSON-RPC when using stdio transport.
    print("Starting ProjectLens MCP server (stdio)...", file=sys.stderr)
    server = create_mcp_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
