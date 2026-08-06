"""MemoryApprovalGateway: audited create/decide memory proposal tools."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.domain.memory import MemoryProposal, MemoryType
from project_lens.domain.models import ClaimType, ProjectRef
from project_lens.main import create_app
from project_lens.runtime.lifecycle import LifecycleEventType
from project_lens.runtime.memory_approval_gateway import MemoryApprovalGateway
from project_lens.runtime.policy import RiskClass
from project_lens.runtime.tool_specs import ToolLane, assert_tool_callable


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _proposal(**overrides: object) -> MemoryProposal:
    payload: dict[str, object] = {
        "project": _project(),
        "proposed_by": "u1",
        "claim_text": "Ada owns order-service",
        "claim_type": ClaimType.FACT,
        "memory_type": MemoryType.OWNER,
        "evidence_ids": (uuid4(),),
        "reason": "verified fact",
    }
    payload.update(overrides)
    return MemoryProposal.model_validate(payload)


def test_memory_gateway_refuses_engineering_allow_apply() -> None:
    with pytest.raises(ValueError, match="allow_apply"):
        MemoryApprovalGateway(InMemoryMemoryStore(), allow_apply=True)


def test_memory_gateway_create_does_not_write_memory() -> None:
    store = InMemoryMemoryStore()
    gateway = MemoryApprovalGateway(store)
    created = gateway.create_memory_proposal(_proposal())
    assert created.status == "pending"
    assert store.list_memories(_project()) == ()
    assert [event.tool_name for event in gateway.audit_events] == [
        "create_memory_proposal"
    ]
    assert gateway.audit_events[0].risk_class == RiskClass.VALIDATE
    assert gateway.audit_events[0].arguments["writes_project_memory"] is False
    assert gateway.audit_summary()["engineering_apply_events"] == []


def test_memory_gateway_decide_approve_and_reject_are_audited() -> None:
    store = InMemoryMemoryStore()
    gateway = MemoryApprovalGateway(store)
    created = gateway.create_memory_proposal(_proposal())
    proposal, memory = gateway.decide_memory_proposal(
        created.id,
        approved=True,
        decided_by="approver",
    )
    assert proposal.status == "approved"
    assert memory is not None
    assert store.list_memories(_project())
    names = [event.tool_name for event in gateway.audit_events]
    assert names == ["create_memory_proposal", "decide_memory_proposal"]
    decide = gateway.audit_events[-1]
    assert decide.risk_class == RiskClass.APPLY
    assert decide.arguments["engineering_apply"] is False
    assert decide.arguments["writes_project_memory"] is True

    rejected = gateway.create_memory_proposal(
        _proposal(claim_text="other claim", evidence_ids=(uuid4(),))
    )
    rejected_prop, rejected_mem = gateway.decide_memory_proposal(
        rejected.id,
        approved=False,
        decided_by="approver",
    )
    assert rejected_prop.status == "rejected"
    assert rejected_mem is None
    assert gateway.audit_events[-1].arguments["writes_project_memory"] is False


def test_api_memory_routes_use_approval_gateway_audit() -> None:
    app = create_app()
    client = TestClient(app)
    evidence_id = str(uuid4())
    created = client.post(
        "/api/v1/projects/memory-proposals",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
            },
            "proposed_by": "u1",
            "claim_text": "Ada owns order-service",
            "evidence_ids": [evidence_id],
            "reason": "api audit",
        },
    )
    assert created.status_code == 201, created.text
    proposal_id = created.json()["id"]
    gateway = app.state.memory_approval_gateway
    assert "create_memory_proposal" in [
        event.tool_name for event in gateway.audit_events
    ]

    decided = client.post(
        f"/api/v1/memory-proposals/{proposal_id}/decision",
        json={"approved": True, "decided_by": "approver"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["memory"] is not None
    assert "decide_memory_proposal" in [
        event.tool_name for event in gateway.audit_events
    ]
    assert gateway.audit_summary()["engineering_apply_events"] == []

    types = {event.type for event in app.state.lifecycle_bus.all()}
    assert LifecycleEventType.MEMORY_PROPOSED in types
    assert LifecycleEventType.MEMORY_APPROVED in types


def test_decide_memory_proposal_callable_without_engineering_apply() -> None:
    assert_tool_callable("decide_memory_proposal", allow_apply=False)
    assert_tool_callable("create_memory_proposal", allow_apply=False)
    specs = MemoryApprovalGateway(InMemoryMemoryStore()).list_approval_specs()
    assert any(item.tool_name == "decide_memory_proposal" for item in specs)
    assert all(item.category == ToolLane.APPROVAL for item in specs)


def test_failed_decide_is_audited() -> None:
    gateway = MemoryApprovalGateway(InMemoryMemoryStore())
    with pytest.raises(KeyError):
        gateway.decide_memory_proposal(
            uuid4(),
            approved=True,
            decided_by="approver",
        )
    assert gateway.audit_events[-1].ok is False
    assert gateway.audit_events[-1].tool_name == "decide_memory_proposal"
