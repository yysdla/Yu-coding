"""Multi-turn conversation session contracts for the Agent Harness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from project_lens.domain.models import ProjectRef, utc_now

DEFAULT_RECENT_TURN_LIMIT = 8
# Mid-window compression: when pool exceeds limit, move 1-based turns 5–8 into summary.
COMPRESS_TURN_START_1BASED = 5
COMPRESS_TURN_END_1BASED = 8
# Answer summary + evidence ids share this budget; user question is excluded.
COMPRESSED_ANSWER_TOKEN_BUDGET = 300
DEFAULT_SESSION_TTL = timedelta(days=7)
CITATION_SHORT_TITLE_MAX = 120


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConversationTurn(FrozenModel):
    run_id: UUID | None = None
    user_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=20_000)
    rewritten_question: str | None = Field(default=None, max_length=20_000)
    # Populated at record time so mid-window compression can keep per-turn answer+evidence.
    answer_summary: str | None = Field(default=None, max_length=4_000)
    evidence_ids: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=utc_now)


class CompressedTurnRecord(FrozenModel):
    """One L1 turn compacted into L2 (user question + budgeted answer/evidence)."""

    user_question: str = Field(min_length=1, max_length=20_000)
    answer_summary: str = Field(default="", max_length=4_000)
    evidence_ids: tuple[str, ...] = ()
    occurred_at: datetime = Field(default_factory=utc_now)
    run_id: UUID | None = None


class DateWindow(FrozenModel):
    """Optional session-level date filter. None / unset means whole pool (S07 fills UI)."""

    start: datetime | None = None
    end: datetime | None = None


class DefaultContextKind(StrEnum):
    SUMMARY = "summary"
    RECENT_TURN = "recent_turn"


class DefaultContextItem(FrozenModel):
    """One default pending-context row for preview/assembly (S02 enumerate → S03 consume)."""

    kind: DefaultContextKind
    occurred_at: datetime
    label: str = Field(min_length=1, max_length=500)
    run_id: UUID | None = None
    evidence_ids: tuple[str, ...] = ()
    user_question: str | None = Field(default=None, max_length=20_000)
    answer_summary: str | None = Field(default=None, max_length=4_000)


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
    # Per-turn records produced by mid-window compression (S02).
    compressed_turn_records: tuple[CompressedTurnRecord, ...] = ()
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


def estimate_text_tokens(text: str) -> int:
    """Cheap token estimate aligned with workflow providers (≈4 chars / token)."""

    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def clip_text_to_token_budget(text: str, budget: int) -> str:
    """Clip text so estimate_text_tokens(result) <= budget (budget<=0 → empty)."""

    if budget <= 0 or not text:
        return ""
    if estimate_text_tokens(text) <= budget:
        return text
    # Binary-search character length; estimate is monotonic in len for this helper.
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip()
        if estimate_text_tokens(candidate) <= budget:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    if best and len(best) < len(text):
        return best.rstrip() + "…"
    return best


def clip_answer_and_evidence_to_budget(
    answer_summary: str,
    evidence_ids: tuple[str, ...] | list[str],
    *,
    budget: int = COMPRESSED_ANSWER_TOKEN_BUDGET,
) -> tuple[str, tuple[str, ...]]:
    """Prefer keeping evidence ids; clip answer summary to remaining token budget."""

    ids = tuple(str(item) for item in evidence_ids if str(item).strip())
    if budget <= 0:
        return "", ()
    kept_ids: list[str] = []
    used = 0
    for item in ids:
        cost = estimate_text_tokens(item if not kept_ids else f",{item}")
        if used + cost > budget:
            break
        kept_ids.append(item)
        used += cost
    remaining = budget - used
    clipped = clip_text_to_token_budget(answer_summary or "", remaining)
    return clipped, tuple(kept_ids)


def split_recent_turns_for_compression(
    recent: tuple[ConversationTurn, ...],
    *,
    recent_turn_limit: int,
    compress_start_1based: int = COMPRESS_TURN_START_1BASED,
    compress_end_1based: int = COMPRESS_TURN_END_1BASED,
) -> tuple[tuple[ConversationTurn, ...], tuple[ConversationTurn, ...]]:
    """Split (to_compress, kept) after appending a turn.

    Product rule (limit >= 8): when len > 8, compress 1-based turns 5–8.
    Smaller custom limits keep legacy FIFO overflow for existing unit tests.
    """

    if len(recent) <= recent_turn_limit:
        return (), recent
    start = compress_start_1based - 1
    end = compress_end_1based
    if (
        recent_turn_limit >= compress_end_1based
        and len(recent) > end
        and 0 <= start < end
    ):
        return recent[start:end], recent[:start] + recent[end:]
    return recent[:-recent_turn_limit], recent[-recent_turn_limit:]


def _in_date_window(occurred_at: datetime, date_window: DateWindow | None) -> bool:
    if date_window is None:
        return True
    stamp = occurred_at.astimezone(timezone.utc)
    if date_window.start is not None and stamp < date_window.start.astimezone(timezone.utc):
        return False
    if date_window.end is not None and stamp > date_window.end.astimezone(timezone.utc):
        return False
    return True


def enumerate_default_context_items(
    session: ConversationSession,
    *,
    date_window: DateWindow | None = None,
) -> tuple[DefaultContextItem, ...]:
    """Default pending pool: compressed summaries + recent turns (no strip-non-today).

    ``date_window=None`` keeps the whole pool. Filtering is a hook for S07.
    """

    items: list[DefaultContextItem] = []
    for record in session.summary.compressed_turn_records:
        if not _in_date_window(record.occurred_at, date_window):
            continue
        label = make_short_title(record.user_question, max_len=80)
        items.append(
            DefaultContextItem(
                kind=DefaultContextKind.SUMMARY,
                occurred_at=record.occurred_at,
                label=f"[summary] {label}",
                run_id=record.run_id,
                evidence_ids=record.evidence_ids,
                user_question=record.user_question,
                answer_summary=record.answer_summary,
            )
        )
    for turn in session.recent_turns:
        if not _in_date_window(turn.created_at, date_window):
            continue
        items.append(
            DefaultContextItem(
                kind=DefaultContextKind.RECENT_TURN,
                occurred_at=turn.created_at,
                label=make_short_title(turn.text, max_len=80),
                run_id=turn.run_id,
                evidence_ids=turn.evidence_ids,
                user_question=turn.text,
                answer_summary=turn.answer_summary,
            )
        )
    items.sort(key=lambda item: item.occurred_at.astimezone(timezone.utc))
    return tuple(items)
