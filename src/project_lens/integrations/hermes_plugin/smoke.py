"""Smoke-test helpers for the exported Hermes ProjectLens plugin."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import argparse
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any
from contextlib import redirect_stdout
from io import StringIO

from project_lens.integrations.hermes_plugin.client import ProjectLensApiClient
from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig
from project_lens.integrations.hermes_plugin.tools import (
    FORMAL_TOOL_NAMES,
    TOOLSET_NAME,
    build_tool_call_payload,
)


CommandHandler = Callable[[str], str | None]


@dataclass(frozen=True)
class HermesPluginSmokeResult:
    ok: bool
    output: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class HermesCliCommandSmokeResult:
    ok: bool
    output: str
    process_command_return: bool


@dataclass(frozen=True)
class ProjectLensToolLoopSmokeResult:
    ok: bool
    output: str
    discovered_tools: tuple[str, ...]
    called_tools: tuple[str, ...]
    tool_results: tuple[dict[str, Any], ...]


class _SmokeHermesContext:
    def __init__(self) -> None:
        self.commands: dict[str, CommandHandler] = {}
        self.tools: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def register_command(
        self,
        name: str,
        handler: CommandHandler,
        description: str = "",
        args_hint: str = "",
    ) -> None:
        del description, args_hint
        self.commands[name] = handler

    def register_tool(self, *args: Any, **kwargs: Any) -> None:
        # Tool Envelope second cut: formal projectlens_* tools may register when
        # Hermes ctx already exposes register_tool. Skills remain forbidden.
        self.tools.append((args, kwargs))

    def register_skill(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("ProjectLens Hermes plugin must not register skills.")


def run_exported_plugin_smoke(
    *,
    plugin_dir: Path,
    api_base_url: str,
    question: str,
    tenant_id: str,
    project_id: str,
) -> HermesPluginSmokeResult:
    """Load an exported Hermes plugin and execute its ``/project`` command."""

    module_path = plugin_dir / "__init__.py"
    if not module_path.is_file():
        raise FileNotFoundError(f"Exported Hermes plugin bootstrap not found: {module_path}")

    with _temporary_env(
        {
            "PROJECTLENS_API_BASE_URL": api_base_url.rstrip("/"),
            "PROJECTLENS_DEFAULT_TENANT_ID": tenant_id,
            "PROJECTLENS_DEFAULT_PROJECT_ID": project_id,
        }
    ):
        module = _load_module(module_path)
        ctx = _SmokeHermesContext()
        module.register(ctx)
        handler = ctx.commands.get("project")
        if handler is None:
            raise RuntimeError("Exported ProjectLens plugin did not register /project.")
        output = handler(question) or ""
    return HermesPluginSmokeResult(
        ok=bool(output.strip()),
        output=output,
        commands=tuple(sorted(ctx.commands)),
    )


def run_hermes_cli_command_smoke(
    *,
    hermes_repo: Path,
    hermes_home: Path,
    api_base_url: str,
    tenant_id: str,
    project_id: str,
    command: str,
) -> HermesCliCommandSmokeResult:
    """Execute Hermes' real ``HermesCLI.process_command`` slash path."""

    cli_path = hermes_repo / "cli.py"
    if not cli_path.is_file():
        raise FileNotFoundError(f"Hermes cli.py not found: {cli_path}")
    env_updates = {
        "HERMES_HOME": str(hermes_home),
        "PROJECTLENS_API_BASE_URL": api_base_url.rstrip("/"),
        "PROJECTLENS_DEFAULT_TENANT_ID": tenant_id,
        "PROJECTLENS_DEFAULT_PROJECT_ID": project_id,
    }
    with _temporary_env(env_updates), _temporary_syspath(hermes_repo):
        module = _load_module_as("hermes_cli_smoke_cli", cli_path)
        cli_cls = getattr(module, "HermesCLI", None)
        if cli_cls is None:
            raise RuntimeError(f"HermesCLI class not found in: {cli_path}")
        cli = cli_cls(compact=True, verbose=False)
        buffer = StringIO()
        with redirect_stdout(buffer):
            result = bool(cli.process_command(command))
    output = buffer.getvalue()
    return HermesCliCommandSmokeResult(
        ok=result and bool(output.strip()),
        output=output,
        process_command_return=result,
    )


def run_projectlens_tool_loop_smoke(
    *,
    api_base_url: str,
    tenant_id: str,
    project_id: str,
    tool_calls: Sequence[dict[str, Any]],
    user_id: str = "hermes-user",
    chat_id: str = "hermes-chat",
) -> ProjectLensToolLoopSmokeResult:
    """Smoke-test the formal ProjectLens tool-loop path over HTTP.

    This simulates the path Hermes' Agent loop should use:
    GET /project-agent/tools -> POST /project-agent/tools/call.
    It intentionally does not call /project-agent/ask, because ask is debug/smoke.
    """

    config = ProjectLensPluginConfig(
        api_base_url=api_base_url.rstrip("/"),
        default_tenant_id=tenant_id,
        default_project_id=project_id,
        default_user_id=user_id,
    )
    client = ProjectLensApiClient(config)
    lines: list[str] = []
    catalog = client.list_tools()
    tools = catalog.get("tools") if isinstance(catalog, dict) else None
    if catalog.get("ok") is not True or not isinstance(tools, list):
        output = _format_tool_loop_failure(
            "GET /project-agent/tools failed or returned an invalid catalog.",
            catalog,
        )
        return ProjectLensToolLoopSmokeResult(False, output, (), (), ())

    discovered = tuple(
        str(item.get("name"))
        for item in tools
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    lines.append(f"GET /project-agent/tools ok=true tools={len(discovered)}")
    mismatch = set(discovered) != set(FORMAL_TOOL_NAMES)
    unsafe = any(
        not isinstance(item, dict)
        or item.get("allow_apply") is not False
        or item.get("category") != "read"
        for item in tools
    )
    if mismatch or unsafe:
        lines.append(
            "formal tool catalog mismatch: "
            f"got={sorted(discovered)} expected={sorted(FORMAL_TOOL_NAMES)}"
        )
        lines.append("Only the five read-only projectlens_* tools may enter Hermes tool loop.")
        return ProjectLensToolLoopSmokeResult(
            False,
            "\n".join(lines),
            tuple(sorted(discovered)),
            (),
            (),
        )

    called: list[str] = []
    results: list[dict[str, Any]] = []
    for step in tool_calls:
        tool_name = str(step.get("tool_name") or "")
        arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        if tool_name not in FORMAL_TOOL_NAMES:
            lines.append(f"blocked non-formal tool: {tool_name or '<empty>'}")
            return ProjectLensToolLoopSmokeResult(
                False,
                "\n".join(lines),
                tuple(sorted(discovered)),
                tuple(called),
                tuple(results),
            )
        payload = build_tool_call_payload(
            tool_name=tool_name,
            arguments=arguments,
            config=config,
            chat_id=chat_id,
            user_id=user_id,
        )
        envelope = client.call_tool(payload)
        results.append(envelope)
        called.append(tool_name)
        allow_apply = bool(envelope.get("audit_ref", {}).get("allow_apply", False))
        ok_text = str(bool(envelope.get("ok"))).lower()
        apply_text = str(allow_apply).lower()
        lines.append(
            f"POST /project-agent/tools/call {tool_name} "
            f"ok={ok_text} allow_apply={apply_text}"
        )
        if envelope.get("ok") is not True or allow_apply:
            hint = envelope.get("agent_recovery_hint")
            if hint:
                lines.append(f"agent_recovery_hint={hint}")
            return ProjectLensToolLoopSmokeResult(
                False,
                "\n".join(lines),
                tuple(sorted(discovered)),
                tuple(called),
                tuple(results),
            )

    return ProjectLensToolLoopSmokeResult(
        True,
        "\n".join(lines),
        tuple(sorted(discovered)),
        tuple(called),
        tuple(results),
    )


@dataclass(frozen=True)
class HermesToolRegistrySmokeResult:
    ok: bool
    output: str
    discovered_tools: tuple[str, ...]
    dispatched_tool: str | None
    envelope: dict[str, Any] | None


def run_hermes_projectlens_tool_registry_smoke(
    *,
    hermes_repo: Path,
    api_base_url: str,
    tenant_id: str = "demo",
    project_id: str = "payment",
    user_id: str = "hermes-user",
    chat_id: str = "hermes-chat",
    hermes_home: Path | None = None,
    query: str = "create_order coupon",
) -> HermesToolRegistrySmokeResult:
    """Load ProjectLens via Hermes PluginManager and dispatch one formal tool.

    Does not call /project-agent/ask or /project slash commands. Does not modify
    Hermes core — only enables the existing projectlens plugin and uses
    tools.registry.dispatch.
    """

    hermes_repo = hermes_repo.resolve()
    plugin_dir = hermes_repo / "plugins" / "projectlens"
    if not (plugin_dir / "__init__.py").is_file():
        return HermesToolRegistrySmokeResult(
            False,
            f"Hermes projectlens plugin not found: {plugin_dir}",
            (),
            None,
            None,
        )

    lines: list[str] = []
    home = hermes_home or Path(tempfile.mkdtemp(prefix="hermes-projectlens-tools-"))
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.yaml"
    config_path.write_text(
        "plugins:\n  enabled:\n    - projectlens\n",
        encoding="utf-8",
    )

    env_updates = {
        "HERMES_HOME": str(home),
        "PROJECTLENS_API_BASE_URL": api_base_url.rstrip("/"),
        "PROJECTLENS_DEFAULT_TENANT_ID": tenant_id,
        "PROJECTLENS_DEFAULT_PROJECT_ID": project_id,
        "PROJECTLENS_DEFAULT_USER_ID": user_id,
        "PROJECTLENS_FEISHU_CARDS": "0",
    }

    with _temporary_env(env_updates), _temporary_syspath(hermes_repo):
        try:
            import hermes_cli.plugins as hermes_plugins
            from tools.registry import registry
        except ImportError as exc:
            return HermesToolRegistrySmokeResult(
                False,
                f"Unable to import Hermes plugin manager/registry: {exc}",
                (),
                None,
                None,
            )

        # Fresh manager so prior process state does not hide registration failures.
        hermes_plugins._plugin_manager = hermes_plugins.PluginManager()
        hermes_plugins.discover_plugins(force=True)
        manager = hermes_plugins.get_plugin_manager()
        discovered = tuple(
            sorted(
                name
                for name in manager._plugin_tool_names
                if name.startswith("projectlens_")
            )
        )
        lines.append(
            f"Hermes plugin tools discovered={len(discovered)} "
            f"names={list(discovered)}"
        )
        if set(discovered) != set(FORMAL_TOOL_NAMES):
            lines.append(
                "formal tool catalog mismatch: "
                f"got={list(discovered)} expected={sorted(FORMAL_TOOL_NAMES)}"
            )
            return HermesToolRegistrySmokeResult(
                False, "\n".join(lines), discovered, None, None
            )
        for blocked in ("ask_project", "grep", "deploy", "apply", "restart"):
            if any(blocked in name for name in discovered):
                lines.append(f"blocked unsafe/debug tool present: {blocked}")
                return HermesToolRegistrySmokeResult(
                    False, "\n".join(lines), discovered, None, None
                )

        tool_name = "projectlens_search_context"
        entry = registry.get_entry(tool_name)
        if entry is None:
            lines.append(f"registry missing entry for {tool_name}")
            return HermesToolRegistrySmokeResult(
                False, "\n".join(lines), discovered, None, None
            )
        if getattr(entry, "toolset", None) != TOOLSET_NAME:
            lines.append(
                f"toolset mismatch for {tool_name}: "
                f"got={getattr(entry, 'toolset', None)} expected={TOOLSET_NAME}"
            )
            return HermesToolRegistrySmokeResult(
                False, "\n".join(lines), discovered, None, None
            )

        raw = registry.dispatch(
            tool_name,
            {
                "query": query,
                "limit": 3,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "chat_id": chat_id,
                "user_id": user_id,
            },
        )
        envelope = _decode_tool_dispatch_result(raw)
        lines.append(f"registry.dispatch {tool_name} ok={str(bool(envelope.get('ok'))).lower()}")
        if envelope.get("ok") is not True:
            hint = envelope.get("agent_recovery_hint") or envelope.get("error")
            if hint:
                lines.append(f"agent_recovery_hint={hint}")
            return HermesToolRegistrySmokeResult(
                False, "\n".join(lines), discovered, tool_name, envelope
            )
        audit = envelope.get("audit_ref") if isinstance(envelope.get("audit_ref"), dict) else {}
        if audit.get("allow_apply") is not False:
            lines.append("audit_ref.allow_apply must be false")
            return HermesToolRegistrySmokeResult(
                False, "\n".join(lines), discovered, tool_name, envelope
            )
        if not isinstance(envelope.get("citations"), list):
            lines.append("citations missing or not a list")
            return HermesToolRegistrySmokeResult(
                False, "\n".join(lines), discovered, tool_name, envelope
            )
        if not isinstance(envelope.get("evidence_refs"), list):
            lines.append("evidence_refs missing or not a list")
            return HermesToolRegistrySmokeResult(
                False, "\n".join(lines), discovered, tool_name, envelope
            )
        lines.append("citations/evidence_refs/audit_ref present allow_apply=false")
        lines.append("formal path did not use /project-agent/ask")
        return HermesToolRegistrySmokeResult(
            True, "\n".join(lines), discovered, tool_name, envelope
        )


def _decode_tool_dispatch_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": raw[:500]}
        return decoded if isinstance(decoded, dict) else {"ok": False, "error": str(decoded)}
    return {"ok": False, "error": f"unexpected dispatch type: {type(raw).__name__}"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test an exported ProjectLens Hermes plugin against an ask API.",
    )
    parser.add_argument("--plugin-dir", type=Path, default=None)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--question", default="What is this project?")
    parser.add_argument("--tenant-id", default="demo")
    parser.add_argument("--project-id", default="payment")
    parser.add_argument(
        "--hermes-repo",
        type=Path,
        default=None,
        help="When set, run HermesCLI.process_command smoke instead of exported-plugin smoke.",
    )
    parser.add_argument("--hermes-home", type=Path, default=Path.home() / ".hermes")
    parser.add_argument(
        "--command",
        default=None,
        help="Hermes slash command for --hermes-repo mode. Defaults to /project <question>.",
    )
    parser.add_argument(
        "--tool-loop",
        action="store_true",
        help=(
            "Run formal Hermes tool-loop smoke via GET /project-agent/tools and "
            "POST /project-agent/tools/call; does not call /project-agent/ask."
        ),
    )
    parser.add_argument(
        "--hermes-tool-registry",
        action="store_true",
        help=(
            "Load Hermes PluginManager, discover projectlens_* tools, and "
            "dispatch one via tools.registry (no /project ask)."
        ),
    )
    parser.add_argument(
        "--tool-call",
        action="append",
        default=[],
        help=(
            "JSON tool call for --tool-loop, e.g. "
            '\'{"tool_name":"projectlens_search_context","arguments":{"query":"create_order"}}\'. '
            "May be repeated."
        ),
    )
    parser.add_argument("--user-id", default="hermes-user")
    parser.add_argument("--chat-id", default="hermes-chat")
    args = parser.parse_args(argv)
    if args.tool_loop:
        result = run_projectlens_tool_loop_smoke(
            api_base_url=args.api_base_url,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            user_id=args.user_id,
            chat_id=args.chat_id,
            tool_calls=_parse_tool_calls(args.tool_call, default_query=args.question),
        )
        print(result.output)
        return 0 if result.ok else 1
    if args.hermes_tool_registry:
        if args.hermes_repo is None:
            parser.error("--hermes-tool-registry requires --hermes-repo")
        result = run_hermes_projectlens_tool_registry_smoke(
            hermes_repo=args.hermes_repo,
            api_base_url=args.api_base_url,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            user_id=args.user_id,
            chat_id=args.chat_id,
            hermes_home=None,
            query=args.question,
        )
        print(result.output)
        return 0 if result.ok else 1
    if args.hermes_repo is not None:
        result = run_hermes_cli_command_smoke(
            hermes_repo=args.hermes_repo,
            hermes_home=args.hermes_home,
            api_base_url=args.api_base_url,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            command=args.command or f"/project {args.question}",
        )
        print(result.output)
        print(f"process_command_return={str(result.process_command_return).lower()}")
        return 0 if result.ok else 1
    if args.plugin_dir is None:
        parser.error("--plugin-dir is required unless --hermes-repo is set")
    result = run_exported_plugin_smoke(
        plugin_dir=args.plugin_dir,
        api_base_url=args.api_base_url,
        question=args.question,
        tenant_id=args.tenant_id,
        project_id=args.project_id,
    )
    print(result.output)
    print("")
    print("Registered commands: " + ", ".join(result.commands))
    return 0 if result.ok else 1


def _parse_tool_calls(raw_calls: Sequence[str], *, default_query: str) -> tuple[dict[str, Any], ...]:
    if not raw_calls:
        return (
            {
                "tool_name": "projectlens_search_context",
                "arguments": {"query": default_query, "limit": 3},
            },
        )
    calls: list[dict[str, Any]] = []
    for raw in raw_calls:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("--tool-call must decode to a JSON object")
        calls.append(decoded)
    return tuple(calls)


def _format_tool_loop_failure(message: str, envelope: dict[str, Any]) -> str:
    hint = envelope.get("agent_recovery_hint")
    parts = [message]
    if hint:
        parts.append(f"agent_recovery_hint={hint}")
    return "\n".join(parts)


def _load_module(module_path: Path) -> ModuleType:
    return _load_module_as("projectlens_exported_plugin_smoke", module_path)


def _load_module_as(module_name: str, module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load exported plugin module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _temporary_syspath:
    def __init__(self, path: Path) -> None:
        self._path = str(path)
        self._inserted = False

    def __enter__(self) -> None:
        import sys

        if self._path not in sys.path:
            sys.path.insert(0, self._path)
            self._inserted = True

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        if not self._inserted:
            return
        import sys

        try:
            sys.path.remove(self._path)
        except ValueError:
            pass


class _temporary_env:
    def __init__(self, updates: dict[str, str]) -> None:
        self._updates = updates
        self._original: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self._updates.items():
            self._original[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        for key, old_value in self._original.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


if __name__ == "__main__":
    raise SystemExit(main())
