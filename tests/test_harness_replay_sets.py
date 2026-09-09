"""Phase D curated harness replay sets + memory approval boundary.

Legacy workflow-backed replay execute is retired; keep registry/unit probes only.
"""

from __future__ import annotations

import pytest

from project_lens.application.memory_service import propose_memory_from_answer
from project_lens.domain.models import (
    Claim,
    ClaimType,
    EvidenceGrade,
    ProjectAnswer,
    ProjectRef,
)
from project_lens.evaluation.harness_probes import probe_memory_boundary
from project_lens.evaluation.harness_replay_sets import (
    PHASE_D_REPLAY_SETS,
    get_replay_set,
    list_phase_d_replay_sets,
)
from project_lens.main import create_app


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def test_phase_d_replay_set_registry() -> None:
    sets = list_phase_d_replay_sets()
    assert {item.name for item in sets} == set(PHASE_D_REPLAY_SETS)
    for replay_set in sets:
        assert get_replay_set(replay_set.name).steps
        assert replay_set.steps


@pytest.mark.asyncio
async def test_memory_approval_boundary_without_workflow_replay() -> None:
    """Propose -> reject must never leak pending proposal into approved memories."""

    from datetime import datetime, timezone
    from uuid import uuid4

    from project_lens.domain.models import Evidence, EvidenceType, SourceRef

    app = create_app()
    project = _project()
    evidence_id = uuid4()
    evidence = Evidence(
        id=evidence_id,
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="local", source_id="doc-1"),
        content="owner is Ada",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    answer = ProjectAnswer(
        project=project,
        skill="project_knowledge",
        confidence=0.5,
        status="identified",
        business_summary="owner is Ada",
        technical_summary="owner is Ada",
        claims=(
            Claim(
                text="owner is Ada",
                type=ClaimType.FACT,
                evidence_ids=(evidence_id,),
                grade=EvidenceGrade.C,
            ),
        ),
        evidence=(evidence,),
        unknowns=(),
        recommended_actions=(),
    )

    proposal = propose_memory_from_answer(answer, proposed_by="replay-user")
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
