"""Multi-turn conversation session contracts for the Agent Harness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from project_lens.domain.models import ProjectRef, utc_now

DEFAULT_RECENT_TURN_LIMIT = 6
DEFAULT_SESSION_TTL = timedelta(days=7)
CITATION_SHORT_TITLE_MAX = 120


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConversationTurn(FrozenModel):
    run_id: UUID | None = None
    user_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=20_000)
    rewritten_question: str | None = Field(default=None, max_length=20_000)
    created_at: datetime = Field(default_factory=utc_now)


class CitationSourceKind(StrEnum):
    """Where a citation ledger entry came from."""

    TURN = "turn"
    GROUP_MESSAGE = "group_message"
    HISTORY = "history"


class CitationEntry(FrozenModel):
    """Pinned retrieval handle written before compression / fine-select.

    Default context carries only citation_id + short_title + occurred_at.
    Full body lives in body_snapshot and/or external keys (run_id / message_id).
    """

    citation_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=64)
    source_kind: CitationSourceKind
    occurred_at: datetime = Field(default_factory=utc_now)
    short_title: str = Field(min_length=1, max_length=CITATION_SHORT_TITLE_MAX)
    run_id: UUID | None = None
    trace_id: str | None = Field(default=None, max_length=200)
    message_id: str | None = Field(default=None, max_length=200)
    evidence_ids: tuple[str, ...] = ()
    file_refs: tuple[str, ...] = ()
    body_snapshot: str | None = Field(default=None, max_length=20_000)

    @model_validator(mode="after")
    def _require_retrievable_body(self) -> CitationEntry:
        if self.body_snapshot:
            return self
        if self.run_id is not None or self.trace_id or self.message_id:
            return self
        raise ValueError(
            "citation must keep a body_snapshot or an external retrieval key "
            "(run_id / trace_id / message_id)"
        )

    def metadata_line(self) -> str:
        """Assemble one context line without body text."""

        stamp = self.occurred_at.astimezone(timezone.utc).isoformat()
        return f"citation_id={self.citation_id} time={stamp} title={self.short_title}"


class PinnedIds(FrozenModel):
    """IDs and paths that must survive L1->L2 compression."""

    run_ids: tuple[str, ...] = ()
    trace_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    proposal_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()
    incident_ids: tuple[str, ...] = ()
    commit_shas: tuple[str, ...] = ()
    doc_tokens: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()
    symbol_names: tuple[str, ...] = ()


class ConversationSummary(FrozenModel):
    """L2 rolling session summary. Runtime context only — never ProjectMemory."""

    project: ProjectRef
    active_skill: str | None = None
    active_topic: dict[str, str] = Field(default_factory=dict)
    verified_claim_ids: tuple[UUID, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()
    unknowns: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    pinned_ids: PinnedIds = Field(default_factory=PinnedIds)
    compression_cycle: int = 0
    session_intent: str | None = Field(default=None, max_length=500)
    last_assistant_summary: str | None = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationSession(FrozenModel):
    """Harness session spanning Feishu multi-turn collaboration."""

    session_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = Field(min_length=1, max_length=100)
    chat_id: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=100)
    project: ProjectRef
    last_run_id: UUID | None = None
    recent_turns: tuple[ConversationTurn, ...] = ()
    citations: tuple[CitationEntry, ...] = ()
    summary: ConversationSummary
    # L3 scratchpad reserved for later Engineering/Ops process state.
    task_scratchpad: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + DEFAULT_SESSION_TTL
    )

    @property
    def id(self) -> UUID:
        return self.session_id

    @property
    def project_ref(self) -> ProjectRef:
        return self.project


def empty_summary(project: ProjectRef) -> ConversationSummary:
    return ConversationSummary(project=project)


def make_short_title(text: str, *, max_len: int = CITATION_SHORT_TITLE_MAX) -> str:
    """Collapse whitespace and clip to a citation short title."""

    collapsed = " ".join(text.split())
    if not collapsed:
        return "untitled"
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max(1, max_len - 1)].rstrip() + "…"


def format_citation_ledger_for_context(
    citations: tuple[CitationEntry, ...] | list[CitationEntry],
) -> str:
    """Default context fragment: metadata only, never body_snapshot."""

    if not citations:
        return "citation_ledger=(empty)"
    lines = ["citation_ledger:"]
    lines.extend(item.metadata_line() for item in citations)
    return "\n".join(lines)
