from datetime import datetime, timezone
from uuid import uuid4

import pytest

from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.domain.memory import MemoryProposal
from project_lens.domain.models import ClaimType, ProjectRef


def _proposal(**overrides: object) -> MemoryProposal:
    payload = {
        "project": ProjectRef(tenant_id="demo", project_id="payment"),
        "proposed_by": "u1",
        "claim_text": "order-service owner is Ada",
        "claim_type": ClaimType.FACT,
        "evidence_ids": (uuid4(),),
        "reason": "confirmed in architecture doc",
    }
    payload.update(overrides)
    return MemoryProposal.model_validate(payload)


def test_memory_proposal_requires_evidence_ids() -> None:
    store = InMemoryMemoryStore()
    with pytest.raises(ValueError, match="evidence_ids"):
        store.create_proposal(_proposal(evidence_ids=()))


def test_approved_proposal_becomes_project_memory() -> None:
    store = InMemoryMemoryStore()
    proposal = store.create_proposal(_proposal())
    updated, memory = store.decide_proposal(proposal.id, approved=True, decided_by="lead")
    assert updated.status == "approved"
    assert memory is not None
    assert memory.approved_by == "lead"
    assert memory.evidence_ids == proposal.evidence_ids
    assert memory.valid_from <= datetime.now(timezone.utc)
    memories = store.list_memories(proposal.project)
    assert len(memories) == 1
    assert memories[0].text == proposal.claim_text
    assert memories[0].memory_type == proposal.memory_type


def test_rejected_proposal_does_not_create_memory() -> None:
    store = InMemoryMemoryStore()
    proposal = store.create_proposal(_proposal())
    updated, memory = store.decide_proposal(proposal.id, approved=False, decided_by="lead")
    assert updated.status == "rejected"
    assert memory is None
    assert store.list_memories(proposal.project) == ()
