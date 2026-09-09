"""Harness probe report evaluation (workflow replay execute retired)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.domain.memory import MemoryProposal
from project_lens.domain.models import ClaimType, ProjectRef
from project_lens.evaluation.harness_probes import (
    probe_approval_safety,
    probe_memory_boundary,
)
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.tool_gateway import ToolGateway

ROOT = Path(__file__).parents[1]
DEMO = ROOT / "examples" / "payment_service"


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def test_probe_memory_boundary_blocks_pending_leak() -> None:
    store = InMemoryMemoryStore()
    project = _project()
    proposal = store.create_proposal(
        MemoryProposal(
            project=project,
            proposed_by="u1",
            claim_text="owner is Ada",
            claim_type=ClaimType.FACT,
            evidence_ids=(uuid4(),),
        )
    )
    result = probe_memory_boundary(
        store,
        project=project,
        pending_proposal_ids=(proposal.id,),
    )
    assert result.passed is True
    assert store.list_memories(project) == ()


def test_probe_approval_safety_with_gateway() -> None:
    gateway = ToolGateway(EngineeringPolicy(project_root=DEMO, allow_apply=False))
    plan = gateway.create_patch_plan(
        title="noop",
        rationale="probe",
        patches=[
            {
                "path": "src/order_service.py",
                "old_text": "coupon_id = request.coupon.id",
                "new_text": "coupon_id = request.coupon.id",
            }
        ],
    )
    with pytest.raises(PermissionError):
        gateway.apply_patch_plan(plan)
    result = probe_approval_safety(gateway=gateway)
    assert result.passed is True
