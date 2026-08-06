"""Phase 2: Hermes projectlens-safe profile samples + Kernel boundary checks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.application.project_agent_ask import ProjectAgentAskService
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.context.bootstrap import (
    build_registered_context_engine,
    default_local_project_registrations,
    to_project_registrations,
)
from project_lens.integrations.mcp.handler import TOOL_NAME, ask_project
from project_lens.project_space.registry import (
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.events import InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.resolver import ProjectResolver
from tests.investigation_settings import stub_investigation_settings

ROOT = Path(__file__).resolve().parents[1]
HERMES_DIR = ROOT / "config" / "hermes"
CONFIG_PATH = HERMES_DIR / "projectlens-safe.config.yaml"
SOUL_PATH = HERMES_DIR / "SOUL.projectlens-safe.md"

REQUIRED_DISABLED = (
    "terminal",
    "file",
    "code_execution",
    "browser",
    "delegation",
    "memory",
    "skills",
)
ALLOWED_FEISHU_TOOLSETS = frozenset({"mcp-projectlens", "clarify"})
FORBIDDEN_FEISHU_TOOLSETS = frozenset({"hermes-feishu", "terminal", "file"})


def _load_config() -> dict:
    payload = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _ask_service() -> ProjectAgentAskService:
    registrations = default_local_project_registrations(ROOT)
    engine, _index = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    for space in load_project_spaces_from_dir(ROOT / "config" / "projects", base_dir=ROOT):
        if registry.get(space.tenant_id, space.project_id) is None:
            registry.register(space)
    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        lifecycle=lifecycle,
        app_settings=stub_investigation_settings(),
    )
    run_service = RunService(
        InMemoryRunRepository(),
        ProjectWorkflow(
            ProjectResolver(to_project_registrations(registrations)),
            engine,
            lifecycle=lifecycle,
        ),
        event_sink=events,
        lifecycle=lifecycle,
        investigation_agent=agent,
        agent_mode="read_agent",
    )
    return ProjectAgentAskService(run_service=run_service, project_registry=registry)


def test_phase2_config_files_exist() -> None:
    assert CONFIG_PATH.is_file()
    assert SOUL_PATH.is_file()
    assert (HERMES_DIR / "README.md").is_file()


def test_mcp_server_whitelist_only_ask_project() -> None:
    config = _load_config()
    servers = config.get("mcp_servers") or {}
    assert "projectlens" in servers
    projectlens = servers["projectlens"]
    include = (projectlens.get("tools") or {}).get("include") or []
    assert TOOL_NAME in include
    assert include == [TOOL_NAME]
    args = projectlens.get("args") or []
    assert "project_lens.integrations.mcp.server" in args


def test_feishu_platform_toolsets_are_restricted() -> None:
    config = _load_config()
    feishu = (config.get("platform_toolsets") or {}).get("feishu") or []
    assert feishu
    assert "mcp-projectlens" in feishu
    assert "clarify" in feishu
    assert "hermes-feishu" not in feishu
    for name in feishu:
        assert name in ALLOWED_FEISHU_TOOLSETS
        assert name not in FORBIDDEN_FEISHU_TOOLSETS


def test_disabled_toolsets_cover_dangerous_sets() -> None:
    config = _load_config()
    disabled = set((config.get("agent") or {}).get("disabled_toolsets") or [])
    for name in REQUIRED_DISABLED:
        assert name in disabled, f"missing disabled_toolset: {name}"


def test_profile_routes_point_at_projectlens_safe() -> None:
    config = _load_config()
    routes = config.get("profile_routes") or []
    assert routes
    assert any(
        route.get("platform") == "feishu" and route.get("profile") == "projectlens-safe"
        for route in routes
    )


def test_soul_requires_mcp_and_forbids_invention() -> None:
    text = SOUL_PATH.read_text(encoding="utf-8").casefold()
    assert "projectlens_ask_project" in text
    assert "mcp__projectlens__projectlens_ask_project" in text
    assert "invent" in text or "编造" in text
    assert "terminal" in text
    assert "allow_apply" in text or "apply" in text
    assert "memory" in text


@pytest.mark.asyncio
async def test_phase2_free_question_via_mcp_handler() -> None:
    service = _ask_service()
    result = await ask_project(
        service,
        question="这个项目的主要架构是什么？",
        project_id="payment",
        tenant_id="demo",
    )
    assert result["ok"] is True
    assert result["audit_ref"]["allow_apply"] is False
    assert result["audit_ref"]["agent_mode"] == "read_agent"
    for fact in result["facts"]:
        assert fact.get("citations")
    assert result["facts"] or result["unknowns"]


@pytest.mark.asyncio
async def test_phase2_write_intent_denied_via_mcp_handler() -> None:
    service = _ask_service()
    result = await ask_project(
        service,
        question="请帮我部署并重启服务",
        project_id="payment",
        tenant_id="demo",
    )
    assert result["ok"] is False
    assert result["error_code"] == "WRITE_ACTION_DENIED"
    assert result["audit_ref"]["allow_apply"] is False
