"""Approval harness: allowed_approvers, expiry, audit records, Apply refusal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from project_lens.application.approval_harness import (
    ApprovalError,
    approval_record_from_memory_proposal,
    prepare_decision,
    refuse_engineering_apply,
)
from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.domain.approval import ApprovalKind, ApprovalStatus
from project_lens.domain.memory import MemoryProposal
from project_lens.domain.models import ClaimType, ProjectRef
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.main import create_app
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.tool_gateway import ToolGateway

DEMO = Path(__file__).parents[1] / "examples" / "payment_service"


def _project() -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id="payment")


def _proposal(**overrides: object) -> MemoryProposal:
    payload: dict[str, object] = {
        "project": _project(),
        "proposed_by": "u1",
        "claim_text": "order-service owner is Ada",
        "claim_type": ClaimType.FACT,
        "evidence_ids": (uuid4(),),
        "reason": "doc",
        "allowed_approvers": ("lead", "feishu-lead"),
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
    }
    payload.update(overrides)
    return MemoryProposal.model_validate(payload)


def test_allowed_approver_can_approve() -> None:
    store = InMemoryMemoryStore()
    proposal = store.create_proposal(_proposal())
    updated, memory = store.decide_proposal(
        proposal.id,
        approved=True,
        decided_by="lead",
    )
    assert updated.status == "approved"
    assert updated.decided_by == "lead"
    assert updated.decided_at is not None
    assert memory is not None
    record = approval_record_from_memory_proposal(updated)
    assert record.kind == ApprovalKind.MEMORY_PROPOSAL
    assert record.decision == ApprovalStatus.APPROVED
    assert record.allowed_approvers == ("lead", "feishu-lead")
    assert record.rollback_plan
    assert any(event["type"] == "approval.decided" for event in record.audit_events)


def test_disallowed_approver_is_forbidden() -> None:
    store = InMemoryMemoryStore()
    proposal = store.create_proposal(_proposal())
    with pytest.raises(PermissionError, match="not an allowed approver"):
        store.decide_proposal(proposal.id, approved=True, decided_by="stranger")
    assert store.list_memories(_project()) == ()
    still = store.get_proposal(proposal.id)
    assert still is not None
    assert still.status == "pending"


def test_empty_allow_list_keeps_backward_compatible_any_decider() -> None:
    store = InMemoryMemoryStore()
    proposal = store.create_proposal(_proposal(allowed_approvers=()))
    updated, memory = store.decide_proposal(
        proposal.id,
        approved=True,
        decided_by="anyone",
    )
    assert updated.status == "approved"
    assert memory is not None


def test_expired_proposal_cannot_write_memory() -> None:
    store = InMemoryMemoryStore()
    proposal = store.create_proposal(
        _proposal(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    )
    loaded = store.get_proposal(proposal.id)
    assert loaded is not None
    assert loaded.status == "expired"
    with pytest.raises(ApprovalError, match="expired"):
        store.decide_proposal(proposal.id, approved=True, decided_by="lead")
    assert store.list_memories(_project()) == ()


def test_prepare_decision_rejects_non_pending() -> None:
    proposal = _proposal()
    decided = prepare_decision(proposal, approved=False, decided_by="lead")
    with pytest.raises(ApprovalError, match="already rejected"):
        prepare_decision(decided, approved=True, decided_by="lead")


def test_engineering_apply_still_refused_by_harness() -> None:
    with pytest.raises(PermissionError, match="disabled|approval"):
        refuse_engineering_apply()
    gateway = ToolGateway(EngineeringPolicy(project_root=DEMO, allow_apply=True))
    plan = gateway.create_patch_plan(
        title="noop",
        rationale="still blocked",
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


def test_api_decision_enforces_approver_and_expiry() -> None:
    app = create_app()
    client = TestClient(app)
    create = client.post(
        "/api/v1/projects/memory-proposals",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "proposed_by": "u1",
            "claim_text": "owner is Ada",
            "evidence_ids": [str(uuid4())],
            "allowed_approvers": ["lead"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        },
    )
    assert create.status_code == 201
    proposal_id = create.json()["id"]

    forbidden = client.post(
        f"/api/v1/memory-proposals/{proposal_id}/decision",
        json={"approved": True, "decided_by": "intruder"},
    )
    assert forbidden.status_code == 403

    ok = client.post(
        f"/api/v1/memory-proposals/{proposal_id}/decision",
        json={"approved": True, "decided_by": "lead"},
    )
    assert ok.status_code == 200
    assert ok.json()["proposal"]["status"] == "approved"
    assert ok.json()["memory"]["approved_by"] == "lead"

    expired_create = client.post(
        "/api/v1/projects/memory-proposals",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "proposed_by": "u1",
            "claim_text": "stale fact",
            "evidence_ids": [str(uuid4())],
            "allowed_approvers": ["lead"],
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
        },
    )
    assert expired_create.status_code == 201
    expired_id = expired_create.json()["id"]
    expired_decision = client.post(
        f"/api/v1/memory-proposals/{expired_id}/decision",
        json={"approved": True, "decided_by": "lead"},
    )
    assert expired_decision.status_code == 409
    assert "expired" in expired_decision.json()["detail"]


def test_feishu_card_respects_allowed_approvers() -> None:
    app = create_app()
    verifier = app.state.feishu_event_service._verifier
    verifier._verification_token = "project-lens-local-token"
    verifier._signing_secret = None
    app.state.feishu_event_service._identity_mapper = parse_project_bindings(
        "",
        default_project=ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        ),
    )
    app.state.feishu_messenger = RecordingFeishuMessenger()
    app.state.feishu_event_service._messenger = app.state.feishu_messenger
    app.state.feishu_event_service._memory_store = app.state.memory_store

    proposal = app.state.memory_store.create_proposal(
        _proposal(allowed_approvers=("feishu-lead",))
    )
    client = TestClient(app)
    denied = client.post(
        "/api/v1/feishu/events",
        json={
            "schema": "2.0",
            "token": "project-lens-local-token",
            "header": {
                "event_id": "approval-deny",
                "event_type": "card.action.trigger",
                "tenant_key": "demo",
            },
            "event": {
                "operator": {"user_id": "random-user"},
                "action": {
                    "tag": "button",
                    "value": {
                        "action": "memory_approve",
                        "proposal_id": str(proposal.id),
                    },
                },
                "context": {"open_chat_id": "chat-1"},
            },
        },
    )
    assert denied.status_code == 403
    assert app.state.memory_store.list_memories(_project()) == ()

    allowed = client.post(
        "/api/v1/feishu/events",
        json={
            "schema": "2.0",
            "token": "project-lens-local-token",
            "header": {
                "event_id": "approval-allow",
                "event_type": "card.action.trigger",
                "tenant_key": "demo",
            },
            "event": {
                "operator": {"user_id": "feishu-lead"},
                "action": {
                    "tag": "button",
                    "value": {
                        "action": "memory_approve",
                        "proposal_id": str(proposal.id),
                    },
                },
                "context": {"open_chat_id": "chat-1"},
            },
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "accepted"
    assert app.state.memory_store.list_memories(_project())
