"""Live OpenAI tool-loop provider + SkillGuide for investigation agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_lens.agent.draft import AnswerDraft
from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.agent.openai_loop_provider import ScriptedTransport
from project_lens.agent.provider_factory import create_investigation_provider
from project_lens.agent.skill_guides import select_skill_guide
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.context.bootstrap import (
    build_registered_context_engine,
    default_local_project_registrations,
    to_project_registrations,
)
from project_lens.domain.models import ClaimType, ProjectRef, RunStatus
from project_lens.integrations.feishu.cards import render_answer_card
from project_lens.integrations.feishu.views import AnswerView, select_answer_view
from project_lens.project_space.registry import registry_from_local_registrations
from project_lens.runtime.events import AgentEventType, InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.resolver import ProjectResolver
from tests.investigation_settings import (
    IsolatedInvestigationSettings,
    stub_investigation_settings,
)
from tests.test_feishu_answer_views import _answer, _evidence, _run

ROOT = Path(__file__).resolve().parents[1]


def test_skill_guide_is_optional_not_routing_bucket() -> None:
    assert select_skill_guide("介绍一下这个项目").name == "project_intro"
    assert select_skill_guide("AttributeError on coupon").name == "incident"
    free = select_skill_guide("order_service.py 里 create_order 为什么要检查 coupon？")
    assert free.name == "free_question"


def test_answer_draft_parses_fenced_json() -> None:
    draft = AnswerDraft.from_json_text(
        '这里是说明\n```json\n{"facts":[{"text":"a","citations":[]}],'
        '"unknowns":[],"business_summary":"s"}\n```'
    )
    assert draft.facts[0].text == "a"
    assert draft.business_summary == "s"


def test_investigation_answer_view_title() -> None:
    run = _run("order_service.py 里 create_order 为什么要检查 coupon？")
    answer = _answer(skill="project_investigation", evidence=(_evidence(),))
    assert select_answer_view(run, answer) == AnswerView.REASON_ANALYSIS
    card = render_answer_card(run, answer)
    assert card["header"]["title"]["content"] == "ProjectLens 原因分析"
    joined = "\n".join(item.get("content", "") for item in card["elements"] if "content" in item)
    assert "**一句话结论**" in joined
    assert "SkillGuide" not in joined.split("**调试信息**")[0]
    assert "**项目调查**" not in joined.split("**调试信息**")[0]
    assert "**当前 Skill**" not in joined


def test_provider_factory_defaults_to_stub() -> None:
    provider, meta = create_investigation_provider(
        "q",
        app_settings=stub_investigation_settings(),
    )
    assert meta["investigation_provider"] == "stub_planner"
    assert meta["live_effective"] is False
    assert provider.__class__.__name__ == "InvestigationStubProvider"


@pytest.mark.asyncio
async def test_live_openai_loop_provider_drives_tool_calls() -> None:
    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    transport = ScriptedTransport(
        [
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-search",
                                    "type": "function",
                                    "function": {
                                        "name": "search_context",
                                        "arguments": '{"query":"create_order coupon","limit":5}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {"total_tokens": 20},
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"facts":[{"text":"live tool path ok",'
                                '"citations":[]}],"unknowns":["need citation"],'
                                '"business_summary":"live investigation",'
                                '"technical_summary":"openai_loop","tools_used":'
                                '["search_context"],"next_actions":[]}'
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 15},
            },
        ]
    )
    live_settings = IsolatedInvestigationSettings(
        model_provider="openai",
        model_live=True,
        model_fallback_to_stub=True,
        model_openai_api_key="sk-test-not-real",
        model_openai_model="gpt-test",
        model_name="gpt-test",
        agent_mode="read_agent",
    )
    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        lifecycle=lifecycle,
        app_settings=live_settings,
        chat_transport=transport,
    )
    service = RunService(
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
    run = service.create(
        project=ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        ),
        user_id="u1",
        question="create_order 和 coupon 的关系是什么？",
    )
    executed = await service.execute(run.id)
    assert executed is not None
    assert executed.status == RunStatus.COMPLETED
    assert executed.answer is not None
    assert "search_context" in agent.last_tool_names
    assert agent.last_provider_meta.get("investigation_provider") == "openai_loop"
    assert agent.last_skill_guide == "free_question"
    assert len(transport.calls) == 2
    assert "tools" in transport.calls[0]["body"]
    assert not any(c.type == ClaimType.FACT and not c.evidence_ids for c in executed.answer.claims)
    assert "live investigation" in executed.answer.business_summary or executed.answer.unknowns


@pytest.mark.asyncio
async def test_live_provider_failure_falls_back_to_stub() -> None:
    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    transport = ScriptedTransport([])
    transport.error = RuntimeError("simulated network down")

    class _FailingTransport(ScriptedTransport):
        def post_json(self, url, *, headers, body, timeout_seconds):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated network down")

    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        lifecycle=lifecycle,
        app_settings=IsolatedInvestigationSettings(
            model_provider="openai",
            model_live=True,
            model_fallback_to_stub=True,
            model_openai_api_key="sk-test-not-real",
            model_openai_model="gpt-test",
            agent_mode="read_agent",
        ),
        chat_transport=_FailingTransport([]),
    )
    service = RunService(
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
    assert agent.last_provider_meta.get("fallback_from") == "openai_loop"
    assert agent.last_provider_meta.get("investigation_provider") == "stub_planner"
    assert "search_context" in agent.last_tool_names
    failed = [
        event
        for event in service.events(executed.id)
        if event.type == AgentEventType.LIFECYCLE
        and (event.payload or {}).get("lifecycle")
        == LifecycleEventType.MODEL_PROVIDER_FAILED.value
    ]
    assert failed


@pytest.mark.asyncio
async def test_live_provider_failure_without_fallback_returns_clear_answer() -> None:
    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)

    class _FailingTransport(ScriptedTransport):
        def post_json(self, url, *, headers, body, timeout_seconds):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated network down")

    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        app_settings=IsolatedInvestigationSettings(
            model_provider="openai",
            model_live=True,
            model_fallback_to_stub=False,
            model_openai_api_key="sk-test-not-real",
            agent_mode="read_agent",
        ),
        chat_transport=_FailingTransport([]),
    )
    service = RunService(
        InMemoryRunRepository(),
        ProjectWorkflow(ProjectResolver(to_project_registrations(registrations)), engine),
        investigation_agent=agent,
        agent_mode="read_agent",
    )
    run = service.create(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        user_id="u1",
        question="随便问一个项目问题",
    )
    executed = await service.execute(run.id)
    assert executed is not None
    assert executed.status == RunStatus.COMPLETED
    assert executed.answer is not None
    assert "未能完成" in executed.answer.business_summary
    assert executed.answer.unknowns


def test_verify_answer_draft_demotes_uncited_facts() -> None:
    from project_lens.agent.draft import AnswerDraft, DraftFact
    from project_lens.agent.read_tools import InvestigationLedger
    from project_lens.agent.verifier import verify_answer_draft

    project = ProjectRef(tenant_id="demo", project_id="payment")
    answer = verify_answer_draft(
        AnswerDraft(
            facts=(
                DraftFact(text="无证据的编造事实", citations=()),
                DraftFact(text="另一条无引用", citations=("not-a-uuid",)),
            ),
            unknowns=(),
            business_summary="should demote",
        ),
        project=project,
        ledger=InvestigationLedger(),
    )
    assert not any(claim.type == ClaimType.FACT for claim in answer.claims)
    assert any("未通过引用校验" in item for item in answer.unknowns)


@pytest.mark.asyncio
async def test_live_tool_schemas_are_read_only_only() -> None:
    from project_lens.agent.read_tools import (
        READ_ONLY_INVESTIGATION_TOOLS,
        WRITE_BLOCKED_TOOL_NAMES,
    )

    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)
    transport = ScriptedTransport(
        [
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-search",
                                    "type": "function",
                                    "function": {
                                        "name": "search_context",
                                        "arguments": '{"query":"coupon","limit":3}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {"total_tokens": 10},
            },
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"facts":[],"unknowns":["need more"],'
                                '"business_summary":"readonly tools only",'
                                '"tools_used":["search_context"]}'
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 8},
            },
        ]
    )
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        lifecycle=LifecycleBus(),
        app_settings=IsolatedInvestigationSettings(
            model_provider="openai",
            model_live=True,
            model_fallback_to_stub=True,
            model_openai_api_key="sk-test-not-real",
            model_openai_model="gpt-test",
            agent_mode="read_agent",
        ),
        chat_transport=transport,
    )
    service = RunService(
        InMemoryRunRepository(),
        ProjectWorkflow(
            ProjectResolver(to_project_registrations(registrations)),
            engine,
        ),
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
        question="create_order 和 coupon 的关系是什么？",
    )
    executed = await service.execute(run.id)
    assert executed is not None
    assert executed.status == RunStatus.COMPLETED
    assert transport.calls
    tool_names: set[str] = set()
    for item in transport.calls[0]["body"].get("tools") or []:
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or item.get("name") or "")
        if name:
            tool_names.add(name)
    assert tool_names
    assert tool_names <= READ_ONLY_INVESTIGATION_TOOLS
    assert tool_names.isdisjoint(WRITE_BLOCKED_TOOL_NAMES)
    assert all(name in READ_ONLY_INVESTIGATION_TOOLS for name in agent.last_tool_names)


@pytest.mark.asyncio
async def test_live_fallback_visible_in_feishu_audit_summary() -> None:
    from project_lens.integrations.feishu.audit_summary import build_feishu_audit_summary

    registrations = default_local_project_registrations(ROOT)
    engine, _ = build_registered_context_engine(registrations)
    registry = registry_from_local_registrations(registrations)

    class _FailingTransport(ScriptedTransport):
        def post_json(self, url, *, headers, body, timeout_seconds):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated network down")

    events = InMemoryEventSink()
    lifecycle = LifecycleBus(event_sink=events)
    agent = ProjectInvestigationAgent(
        context_engine=engine,
        project_registry=registry,
        read_gateway=ReadContextGateway(engine),
        lifecycle=lifecycle,
        app_settings=IsolatedInvestigationSettings(
            model_provider="openai",
            model_live=True,
            model_fallback_to_stub=True,
            model_openai_api_key="sk-test-not-real",
            model_openai_model="gpt-test",
            agent_mode="read_agent",
        ),
        chat_transport=_FailingTransport([]),
    )
    service = RunService(
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
    assert executed.answer is not None
    assert agent.last_provider_meta.get("fallback_from") == "openai_loop"

    summary = build_feishu_audit_summary(
        executed,
        executed.answer,
        events=service.events(executed.id),
    )
    assert summary.agent_mode == "read_agent"
    assert summary.provider in {"stub_planner", "openai_loop"}
    assert summary.fallback_from == "openai_loop"
    assert summary.live_effective in {"False", "false", "0"}
    assert summary.read_tool_names != "unknown"
    debug = summary.to_debug_markdown()
    assert "fallback_from: openai_loop" in debug
    assert "agent_mode: read_agent" in debug
    assert "read_tools:" in debug
