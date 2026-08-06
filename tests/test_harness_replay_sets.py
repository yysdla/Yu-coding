"""Phase D curated harness replay sets + memory approval boundary."""

from __future__ import annotations

import pytest

from project_lens.application.conversation_service import ConversationService
from project_lens.application.memory_service import propose_memory_from_answer
from project_lens.domain.models import ProjectRef
from project_lens.evaluation.harness_probes import probe_memory_boundary
from project_lens.evaluation.harness_replay import replay_harness_conversation
from project_lens.evaluation.harness_replay_sets import (
    PHASE_D_REPLAY_SETS,
    get_replay_set,
    list_phase_d_replay_sets,
)
from project_lens.main import create_app
from project_lens.runtime.tool_specs import tool_policy_hash


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def test_phase_d_replay_set_registry() -> None:
    names = {item.name for item in list_phase_d_replay_sets()}
    assert names == {
        "project_intro",
        "knowledge_gap",
        "graph_path",
        "multi_turn_followup",
    }
    assert get_replay_set("project_intro").expect_authorized_retrieval is True


@pytest.mark.asyncio
@pytest.mark.parametrize("set_name", sorted(PHASE_D_REPLAY_SETS))
async def test_phase_d_replay_sets_pass_harness_probes(set_name: str) -> None:
    replay_set = get_replay_set(set_name)
    app = create_app()
    conversation = app.state.conversation_service
    if replay_set.require_compression_cycle:
        conversation = ConversationService(
            recent_turn_limit=2,
            lifecycle=app.state.lifecycle_bus,
        )
        app.state.conversation_service = conversation

    report = await replay_harness_conversation(
        run_service=app.state.run_service,
        conversation=conversation,
        project=_project(),
        steps=replay_set.steps,
        chat_id=f"replay-{set_name}",
        memory_store=app.state.memory_store,
        context_engine=app.state.context_engine,
        evidence_index=app.state.evidence_index,
        lifecycle=app.state.lifecycle_bus,
    )
    assert report.passed, report.as_dict()
    assert len(report.run_ids) == len(replay_set.steps)

    probe_names = {item.name for item in report.probes.probes}
    assert "context_prompt_trail" in probe_names
    assert "read_gateway_trail" in probe_names
    assert all(item.passed for item in report.probes.probes)

    last = report.steps[-1]
    if replay_set.expect_skills:
        assert last.get("skill") in replay_set.expect_skills or last.get(
            "active_skill"
        ) in replay_set.expect_skills
    if replay_set.expect_followup_rewrite:
        assert report.steps[1].get("followup_rewrite")
        assert "故障诊断" in (report.steps[1].get("followup_rewrite") or "")
    if replay_set.expect_authorized_retrieval:
        assert last.get("retrieval_mode") == "authorized"
    if replay_set.require_compression_cycle:
        by_name = {item.name: item for item in report.probes.probes}
        assert by_name["compression_quality"].evidence.get("compression_cycle", 0) >= 1

    assert last.get("tool_policy_hash") == tool_policy_hash()
    obs = report.observability
    assert obs["模型看到了什么"]["layers"] == ["L0", "L1", "L2", "L3", "L4", "L5"]
    assert obs["这些上下文从哪里来"].get("evidence_source") == "read_context_gateway"
    assert "L0_anchors" in obs["哪些内容不能压缩"]
    assert obs["如果回答错了，能不能 replay"]["replay_api"] == "replay_harness_conversation"
    assert obs["如果回答错了，能不能 replay"]["used_prompt"] is True


@pytest.mark.asyncio
async def test_memory_approval_boundary_replay() -> None:
    """Propose -> reject must never leak pending proposal into L5 memories."""

    app = create_app()
    project = _project()
    report = await replay_harness_conversation(
        run_service=app.state.run_service,
        conversation=app.state.conversation_service,
        project=project,
        steps=get_replay_set("project_intro").steps,
        chat_id="replay-memory-boundary",
        memory_store=app.state.memory_store,
        context_engine=app.state.context_engine,
        evidence_index=app.state.evidence_index,
        lifecycle=app.state.lifecycle_bus,
    )
    assert report.passed, report.as_dict()
    run = app.state.run_service.get(__import__("uuid").UUID(report.run_ids[-1]))
    assert run is not None and run.answer is not None

    proposal = propose_memory_from_answer(run.answer, proposed_by="replay-user")
    assert proposal is not None
    created = app.state.memory_approval_gateway.create_memory_proposal(proposal)
    assert app.state.memory_store.list_memories(project) == ()

    boundary = probe_memory_boundary(
        app.state.memory_store,
        project=project,
        pending_proposal_ids=(created.id,),
    )
    assert boundary.passed is True

    app.state.memory_approval_gateway.decide_memory_proposal(
        created.id,
        approved=False,
        decided_by="approver",
    )
    assert app.state.memory_store.list_memories(project) == ()
    names = [
        event.tool_name for event in app.state.memory_approval_gateway.audit_events
    ]
    assert "create_memory_proposal" in names
    assert "decide_memory_proposal" in names
    assert app.state.memory_approval_gateway.audit_summary()[
        "engineering_apply_events"
    ] == []

    # Re-run with empty memories; pending reject must not appear in L5 pack.
    report2 = await replay_harness_conversation(
        run_service=app.state.run_service,
        conversation=app.state.conversation_service,
        project=project,
        steps=get_replay_set("knowledge_gap").steps,
        chat_id="replay-memory-boundary-2",
        memory_store=app.state.memory_store,
        context_engine=app.state.context_engine,
        evidence_index=app.state.evidence_index,
        lifecycle=app.state.lifecycle_bus,
        pending_proposal_ids=(created.id,),
    )
    assert report2.passed, report2.as_dict()
    pack = app.state.run_service._workflow.last_context_pack
    assert pack is not None
    assert pack.memory_ids == ()
    assert pack.provenance.memory_source == "approved_project_memory"
