"""Multi-turn ConversationSession support for read_agent investigation."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.agent.session_context import (
    build_investigation_session_brief,
    prior_file_paths,
)
from project_lens.application.conversation_service import ConversationService
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.context.bootstrap import (
    build_registered_context_engine,
    default_local_project_registrations,
    to_project_registrations,
)
from project_lens.domain.models import ClaimType, ProjectRef, RunStatus
from project_lens.project_space.registry import (
    load_project_spaces_from_dir,
    registry_from_local_registrations,
)
from project_lens.runtime.events import InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.workflow.followup import FollowupRewriter
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


def _build_stack() -> tuple[RunService, ProjectInvestigationAgent, ConversationService]:
    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
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
    conversation = ConversationService(lifecycle=lifecycle)
    return service, agent, conversation


def test_followup_rewriter_uses_project_investigation_wording() -> None:
    conversation = ConversationService()
    session = conversation.get_or_create(
        tenant_id="demo",
        chat_id="chat-inv",
        user_id="u1",
        project=_project(),
    )
    # Pretend previous investigation answer was recorded.
    from project_lens.domain.models import ProjectAnswer

    answer = ProjectAnswer(
        project=_project(),
        status="identified",
        skill="project_investigation",
        confidence=0.7,
        business_summary="已调查 create_order / coupon",
        technical_summary="tools=search_context,read_project_file",
    )
    session = conversation.record_turn(
        session,
        user_id="u1",
        text="order_service.py 里 create_order 为什么要检查 coupon？",
        rewritten_question=None,
        answer=answer,
    )
    assert session.summary.active_skill == "project_investigation"
    rewritten = FollowupRewriter().rewrite("那是谁改的？", session)
    assert rewritten is not None
    assert "项目调查" in rewritten


@pytest.mark.asyncio
async def test_read_agent_multi_turn_reuses_session_pins() -> None:
    service, agent, conversation = _build_stack()
    project = _project()
    session = conversation.get_or_create(
        tenant_id=project.tenant_id,
        chat_id="chat-read-agent-mt",
        user_id="u1",
        project=project,
    )

    first = service.create(
        project=project,
        user_id="u1",
        channel_id="chat-read-agent-mt",
        question="order_service.py 里 create_order 为什么要检查 coupon？",
    )
    executed1 = await service.execute(first.id, session=session)
    assert executed1 is not None
    assert executed1.status == RunStatus.COMPLETED
    assert executed1.answer is not None
    assert executed1.answer.skill == "project_investigation"
    assert "read_project_file" in agent.last_tool_names
    session = conversation.record_turn(
        session,
        user_id="u1",
        text="order_service.py 里 create_order 为什么要检查 coupon？",
        rewritten_question=None,
        run_id=executed1.id,
        answer=executed1.answer,
    )
    assert session.summary.active_skill == "project_investigation"
    assert prior_file_paths(session)
    brief = build_investigation_session_brief(session)
    assert "pinned_files" in brief or "last_answer" in brief

    followup = FollowupRewriter().rewrite("那是谁改的？", session)
    assert followup is not None
    second = service.create(
        project=project,
        user_id="u1",
        channel_id="chat-read-agent-mt",
        question=followup,
    )
    executed2 = await service.execute(second.id, session=session)
    assert executed2 is not None
    assert executed2.status == RunStatus.COMPLETED
    assert executed2.answer is not None
    assert executed2.answer.skill == "project_investigation"
    assert agent.last_session_used is True
    assert "search_context" in agent.last_tool_names
    # Follow-up should still produce cited facts or actionable unknowns.
    facts = [c for c in executed2.answer.claims if c.type == ClaimType.FACT]
    assert facts or executed2.answer.unknowns
    if facts:
        assert all(c.evidence_ids for c in facts)
