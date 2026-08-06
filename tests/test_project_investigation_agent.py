"""ProjectSpace registry and read-agent investigation path."""

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
from project_lens.integrations.feishu.audit_summary import build_feishu_audit_summary
from project_lens.project_space.registry import (
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.events import AgentEventType, InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.resolver import ProjectResolver
from tests.investigation_settings import stub_investigation_settings

ROOT = Path(__file__).resolve().parents[1]


def test_project_registry_loads_two_spaces() -> None:
    spaces = load_project_spaces_from_dir(ROOT / "config" / "projects", base_dir=ROOT)
    assert {space.project_id for space in spaces} >= {"payment", "crm"}
    registry = registry_from_local_registrations(
        default_local_project_registrations(ROOT)
    )
    for space in spaces:
        if registry.get(space.tenant_id, space.project_id) is None:
            registry.register(space)
    payment = registry.require("demo", "payment")
    crm = registry.require("demo", "crm")
    assert payment.display_name
    assert crm.primary_repository_root is not None
    assert payment.project_id != crm.project_id
    assert len(registry) >= 2


@pytest.mark.asyncio
async def test_read_agent_free_question_calls_tools_and_cites_evidence() -> None:
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
    run = service.create(
        project=ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        ),
        user_id="u1",
        question="order_service.py 里 create_order 为什么要检查 coupon？",
    )
    executed = await service.execute(run.id)
    assert executed is not None
    assert executed.status == RunStatus.COMPLETED
    assert executed.answer is not None
    assert executed.answer.skill == "project_investigation"
    assert agent.last_provider_meta.get("investigation_provider") == "stub_planner"
    assert "search_context" in agent.last_tool_names
    assert "read_project_file" in agent.last_tool_names
    assert agent.last_read_audit.get("read_tool_calls", 0) >= 1
    assert "search_context" in (agent.last_read_audit.get("read_tool_names") or [])
    facts = [c for c in executed.answer.claims if c.type == ClaimType.FACT]
    assert facts
    assert all(c.evidence_ids for c in facts)
    assert executed.answer.evidence
    assert "置信度 0%" not in executed.answer.business_summary

    run_events = service.events(executed.id)
    tool_completed = [
        event
        for event in run_events
        if event.type == AgentEventType.TOOL_COMPLETED
        or (
            event.type == AgentEventType.LIFECYCLE
            and (event.payload or {}).get("lifecycle") == "tool.completed"
        )
    ]
    assert tool_completed
    assert any(
        (event.payload or {}).get("tool") == "search_context" for event in tool_completed
    )
    audit = build_feishu_audit_summary(
        executed,
        executed.answer,
        events=run_events,
    )
    assert audit.agent_mode == "read_agent"
    assert "search_context" in audit.read_tool_names
    assert audit.provider == "stub_planner"
    assert "read_tools:" in audit.to_debug_markdown()


@pytest.mark.asyncio
async def test_uncited_draft_fact_is_demoted() -> None:
    from project_lens.agent.draft import AnswerDraft, DraftFact
    from project_lens.agent.read_tools import InvestigationLedger
    from project_lens.agent.verifier import verify_answer_draft

    draft = AnswerDraft(
        facts=[DraftFact(text="无依据的断言", citations=[])],
        unknowns=[],
        business_summary="x",
        technical_summary="y",
        tools_used=["search_context"],
    )
    answer = verify_answer_draft(
        draft,
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        ledger=InvestigationLedger(),
    )
    assert not any(c.type == ClaimType.FACT for c in answer.claims)
    assert any("未通过引用校验" in item for item in answer.unknowns)


@pytest.mark.asyncio
async def test_default_agent_mode_still_uses_workflow() -> None:
    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        app_settings=stub_investigation_settings(agent_mode="workflow"),
    )
    workflow = ProjectWorkflow(
        ProjectResolver(to_project_registrations(registrations)),
        engine,
    )
    service = RunService(
        InMemoryRunRepository(),
        workflow,
        investigation_agent=agent,
        agent_mode="workflow",
    )
    run = service.create(
        project=ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        ),
        user_id="u1",
        question="介绍一下这个项目",
    )
    executed = await service.execute(run.id)
    assert executed is not None
    assert executed.status == RunStatus.COMPLETED
    assert executed.answer is not None
    assert executed.answer.skill != "project_investigation"
    assert agent.last_tool_names == ()
