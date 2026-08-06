"""Read-agent free-question eval / replay (not Phase D Skill sets)."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.context.bootstrap import (
    build_registered_context_engine,
    default_local_project_registrations,
    to_project_registrations,
)
from project_lens.domain.models import ClaimType, ProjectRef, RunStatus
from project_lens.evaluation.read_agent_free_questions import (
    READ_AGENT_FREE_QUESTIONS,
    get_read_agent_free_question,
    list_read_agent_free_questions,
)
from project_lens.integrations.feishu.audit_summary import build_feishu_audit_summary
from project_lens.integrations.feishu.views import answer_view_title, select_answer_view
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


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _build_service() -> tuple[RunService, ProjectInvestigationAgent, InMemoryEventSink]:
    registrations = default_local_project_registrations(ROOT)
    engine, _index = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    for space in load_project_spaces_from_dir(ROOT / "config" / "projects", base_dir=ROOT):
        if registry.get(space.tenant_id, space.project_id) is None:
            registry.register(space)
    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    read_gateway = ReadContextGateway(engine)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=read_gateway,
        lifecycle=lifecycle,
        app_settings=stub_investigation_settings(),
    )
    workflow = ProjectWorkflow(
        ProjectResolver(to_project_registrations(registrations)),
        engine,
        lifecycle=lifecycle,
    )
    service = RunService(
        InMemoryRunRepository(),
        workflow,
        event_sink=events,
        lifecycle=lifecycle,
        investigation_agent=agent,
        agent_mode="read_agent",
    )
    return service, agent, events


def test_read_agent_free_question_registry() -> None:
    names = {item.name for item in list_read_agent_free_questions()}
    assert names == {
        "coupon_check_in_create_order",
        "order_creation_entrypoint",
        "docs_vs_code_entry",
        "who_changed_order_creation",
    }
    assert "coupon" in get_read_agent_free_question(
        "coupon_check_in_create_order"
    ).question


@pytest.mark.asyncio
@pytest.mark.parametrize("case", READ_AGENT_FREE_QUESTIONS, ids=lambda c: c.name)
async def test_read_agent_free_questions_replay(case) -> None:
    service, agent, _events = _build_service()
    run = service.create(
        project=_project(),
        user_id="replay-user",
        question=case.question,
    )
    executed = await service.execute(run.id)
    assert executed is not None
    assert executed.status == RunStatus.COMPLETED
    assert executed.answer is not None
    assert executed.answer.skill == "project_investigation"

    tools = set(agent.last_tool_names)
    assert "search_context" in tools
    if case.expect_tools_any_of:
        assert tools & set(case.expect_tools_any_of)
    if case.expect_file_read:
        assert tools & {"read_project_file", "read_project_file_range"}

    facts = [c for c in executed.answer.claims if c.type == ClaimType.FACT]
    if facts:
        assert all(c.evidence_ids for c in facts)
        assert executed.answer.evidence
    if case.expect_unknown_when_missing_author:
        assert executed.answer.unknowns
        blob = " ".join(executed.answer.unknowns)
        assert any(token in blob for token in ("作者", "提交", "commit", "谁"))

    view = select_answer_view(executed, executed.answer)
    title = answer_view_title(view)
    assert title.startswith("ProjectLens ")
    assert "项目调查" not in title
    assert view.value != "project_investigation" or title == "ProjectLens 项目回答"

    audit = build_feishu_audit_summary(
        executed,
        executed.answer,
        events=service.events(executed.id),
    )
    assert audit.agent_mode == "read_agent"
    assert audit.provider == "stub_planner"
    assert "search_context" in audit.read_tool_names
    assert "置信度 0%" not in executed.answer.business_summary
