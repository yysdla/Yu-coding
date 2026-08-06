"""Deterministic approval state machine helpers for harness-controlled writes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from project_lens.domain.approval import (
    ApprovalKind,
    ApprovalRecord,
    ApprovalStatus,
)
from project_lens.domain.memory import MemoryProposal


class ApprovalError(ValueError):
    """Raised when an approval transition is illegal or unauthorized."""


def is_expired(proposal: MemoryProposal, *, now: datetime | None = None) -> bool:
    if proposal.expires_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    expires = proposal.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current >= expires


def refresh_expiration(
    proposal: MemoryProposal,
    *,
    now: datetime | None = None,
) -> MemoryProposal:
    """Mark pending proposals expired without writing ProjectMemory."""

    if proposal.status != "pending":
        return proposal
    if not is_expired(proposal, now=now):
        return proposal
    return proposal.model_copy(update={"status": "expired"})


def assert_approver_allowed(proposal: MemoryProposal, decided_by: str) -> None:
    """Empty allow-list means any authenticated decider (backward compatible)."""

    if not proposal.allowed_approvers:
        return
    if decided_by not in proposal.allowed_approvers:
        raise PermissionError(
            f"user {decided_by} is not an allowed approver for this proposal"
        )


def prepare_decision(
    proposal: MemoryProposal,
    *,
    approved: bool,
    decided_by: str,
    now: datetime | None = None,
) -> MemoryProposal:
    """Validate pending -> approved/rejected/expired transition metadata."""

    current = now or datetime.now(timezone.utc)
    refreshed = refresh_expiration(proposal, now=current)
    if refreshed.status == "expired":
        raise ApprovalError("memory proposal has expired")
    if refreshed.status != "pending":
        raise ApprovalError(f"memory proposal is already {refreshed.status}")
    assert_approver_allowed(refreshed, decided_by)
    status = "approved" if approved else "rejected"
    return refreshed.model_copy(
        update={
            "status": status,
            "decided_by": decided_by,
            "decided_at": current,
        }
    )


def approval_record_from_memory_proposal(
    proposal: MemoryProposal,
    *,
    extra_audit: tuple[dict[str, Any], ...] = (),
) -> ApprovalRecord:
    decision = ApprovalStatus(proposal.status)
    events: list[dict[str, Any]] = [
        {
            "type": "approval.requested",
            "proposal_id": str(proposal.id),
            "requested_by": proposal.proposed_by,
            "at": proposal.created_at.isoformat(),
        }
    ]
    if proposal.decided_at is not None and decision != ApprovalStatus.PENDING:
        events.append(
            {
                "type": "approval.decided",
                "proposal_id": str(proposal.id),
                "decided_by": proposal.decided_by,
                "decision": decision.value,
                "at": proposal.decided_at.isoformat(),
            }
        )
    events.extend(extra_audit)
    return ApprovalRecord(
        approval_id=uuid4(),
        kind=ApprovalKind.MEMORY_PROPOSAL,
        proposal_id=proposal.id,
        project=proposal.project,
        requested_by=proposal.proposed_by,
        allowed_approvers=proposal.allowed_approvers,
        decided_by=proposal.decided_by,
        decision=decision,
        expires_at=proposal.expires_at,
        rollback_plan=proposal.rollback_plan,
        audit_events=tuple(events),
        created_at=proposal.created_at,
        decided_at=proposal.decided_at,
    )


def refuse_engineering_apply(*, reason: str = "engineering apply is disabled") -> None:
    """Engineering Apply stays human-controlled and disabled in this cut."""

    raise PermissionError(reason)
