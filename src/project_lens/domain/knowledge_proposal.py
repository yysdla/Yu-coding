"""Obsidian Inbox proposals before human approval."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import Field

from project_lens.domain.models import FrozenModel, ProjectRef


class KnowledgeProposalStatus(StrEnum):
    PROPOSED = "proposed"
    NEEDS_EVIDENCE = "needs_evidence"
    CONFLICTED = "conflicted"
    APPROVED = "approved"
    REJECTED = "rejected"


class KnowledgeProposal(FrozenModel):
    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    project: ProjectRef
    kind: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=50_000)
    source_path: str = Field(min_length=1, max_length=1_000)
    source_hash: str = Field(min_length=16, max_length=128)
    status: KnowledgeProposalStatus = KnowledgeProposalStatus.PROPOSED
    evidence_ids: tuple[str, ...] = ()
    created_by: str = Field(min_length=1, max_length=100)
    decided_by: str | None = Field(default=None, max_length=100)
    decision_reason: str | None = Field(default=None, max_length=2_000)
    created_at: datetime
    decided_at: datetime | None = None

