"""Project memory proposals and approved long-lived memories."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from project_lens.domain.approval import DEFAULT_APPROVAL_TTL
from project_lens.domain.models import ClaimType, FrozenModel, ProjectRef, utc_now


class MemoryType(StrEnum):
    OWNER = "owner"
    ARCHITECTURE_FACT = "architecture_fact"
    DECISION = "decision"
    RISK = "risk"
    RUNBOOK = "runbook"
    POSTMORTEM = "postmortem"
    BUSINESS_RULE = "business_rule"
    TEAM_CONVENTION = "team_convention"


_MEMORY_TYPE_LABELS = {
    MemoryType.OWNER: "负责人",
    MemoryType.ARCHITECTURE_FACT: "架构事实",
    MemoryType.DECISION: "决策",
    MemoryType.RISK: "风险",
    MemoryType.RUNBOOK: "Runbook",
    MemoryType.POSTMORTEM: "复盘",
    MemoryType.BUSINESS_RULE: "业务规则",
    MemoryType.TEAM_CONVENTION: "团队约定",
}


def memory_type_label(memory_type: MemoryType | str) -> str:
    try:
        return _MEMORY_TYPE_LABELS[MemoryType(memory_type)]
    except ValueError:
        return str(memory_type)


def default_memory_proposal_expires_at() -> datetime:
    return utc_now() + DEFAULT_APPROVAL_TTL


def normalize_memory_text(text: str) -> str:
    """Normalize claim text for conflict detection."""

    return "".join(text.casefold().split())


class MemoryProposal(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    project: ProjectRef
    proposed_by: str = Field(min_length=1, max_length=100)
    claim_text: str = Field(min_length=1, max_length=2_000)
    claim_type: ClaimType = ClaimType.FACT
    memory_type: MemoryType = MemoryType.ARCHITECTURE_FACT
    evidence_ids: tuple[UUID, ...] = ()
    reason: str = Field(default="", max_length=2_000)
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    allowed_approvers: tuple[str, ...] = ()
    expires_at: datetime | None = Field(default_factory=default_memory_proposal_expires_at)
    replaces_memory_id: UUID | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    rollback_plan: str | None = Field(
        default="Revoke ProjectMemory.valid_to immediately if the fact is wrong.",
        max_length=2_000,
    )
    created_at: datetime = Field(default_factory=utc_now)


class ProjectMemory(FrozenModel):
    id: UUID = Field(default_factory=uuid4)
    project: ProjectRef
    text: str = Field(min_length=1, max_length=2_000)
    memory_type: MemoryType = MemoryType.ARCHITECTURE_FACT
    evidence_ids: tuple[UUID, ...] = ()
    approved_by: str = Field(min_length=1, max_length=100)
    valid_from: datetime = Field(default_factory=utc_now)
    valid_to: datetime | None = None
    proposal_id: UUID | None = None
