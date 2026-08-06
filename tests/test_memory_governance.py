"""ProjectMemory governance: types, conflict detection, replace/supersede."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from project_lens.application.memory_service import infer_memory_type
from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.domain.memory import MemoryProposal, MemoryType
from project_lens.domain.models import ClaimType, ProjectRef


def _proposal(**overrides: object) -> MemoryProposal:
    payload = {
        "project": ProjectRef(tenant_id="demo", project_id="payment"),
        "proposed_by": "u1",
        "claim_text": "order-service owner is Ada",
        "claim_type": ClaimType.FACT,
        "memory_type": MemoryType.OWNER,
        "evidence_ids": (uuid4(),),
        "reason": "confirmed in architecture doc",
    }
    payload.update(overrides)
    return MemoryProposal.model_validate(payload)


def test_infer_memory_type_covers_plan_taxonomy() -> None:
    assert infer_memory_type("负责人是 Ada") == MemoryType.OWNER
    assert infer_memory_type("架构上 order service 依赖 payment service") == (
        MemoryType.ARCHITECTURE_FACT
    )
    assert infer_memory_type("团队决策：coupon 可选") == MemoryType.DECISION
    assert infer_memory_type("主要风险是 null coupon") == MemoryType.RISK
    assert infer_memory_type("runbook: restart checkout worker") == MemoryType.RUNBOOK
    assert infer_memory_type("故障复盘结论") == MemoryType.POSTMORTEM
    assert infer_memory_type("业务规则：coupon 为空仍必须下单") == MemoryType.BUSINESS_RULE
    assert infer_memory_type("团队约定：PR 需要两人 review") == MemoryType.TEAM_CONVENTION


def test_approved_memory_keeps_memory_type() -> None:
    store = InMemoryMemoryStore()
    proposal = store.create_proposal(_proposal(memory_type=MemoryType.RISK, claim_text="risk A"))
    _updated, memory = store.decide_proposal(proposal.id, approved=True, decided_by="lead")
    assert memory is not None
    assert memory.memory_type == MemoryType.RISK
    assert memory.text == "risk A"


def test_conflict_blocks_duplicate_active_memory_without_replace() -> None:
    store = InMemoryMemoryStore()
    first = store.create_proposal(_proposal())
    store.decide_proposal(first.id, approved=True, decided_by="lead")
    with pytest.raises(ValueError, match="conflicting active project memory"):
        store.create_proposal(_proposal(evidence_ids=(uuid4(),)))


def test_replace_revokes_old_memory_on_approve() -> None:
    store = InMemoryMemoryStore()
    first = store.create_proposal(_proposal(claim_text="owner is Ada"))
    _updated, old = store.decide_proposal(first.id, approved=True, decided_by="lead")
    assert old is not None
    assert len(store.list_memories(first.project)) == 1

    replacement = store.create_proposal(
        _proposal(
            claim_text="owner is Ada",
            evidence_ids=(uuid4(),),
            replaces_memory_id=old.id,
            reason="corrected owner spelling / evidence refresh",
        )
    )
    _updated2, new_memory = store.decide_proposal(
        replacement.id, approved=True, decided_by="lead"
    )
    assert new_memory is not None
    active = store.list_memories(first.project)
    assert len(active) == 1
    assert active[0].id == new_memory.id
    revoked = store.get_memory(old.id)
    assert revoked is not None
    assert revoked.valid_to is not None
    assert revoked.valid_to <= datetime.now(timezone.utc) or revoked.valid_to <= new_memory.valid_from
