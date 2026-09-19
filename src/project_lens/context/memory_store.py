"""In-process and SQLite-backed project memory governance."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from uuid import UUID

from project_lens.application.approval_harness import (
    ApprovalError,
    prepare_decision,
    refresh_expiration,
)
from project_lens.domain.memory import (
    MemoryProposal,
    MemoryStatus,
    ProjectMemory,
    ReviewReason,
    normalize_memory_text,
)
from project_lens.domain.memory_identity import build_fact_key
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


class MemoryStore:
    """Pending proposals and approved project memories."""

    def create_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
        raise NotImplementedError

    def get_proposal(self, proposal_id: UUID) -> MemoryProposal | None:
        raise NotImplementedError

    def get_memory(
        self,
        memory_id: UUID,
        *,
        project: ProjectRef | None = None,
        include_inactive: bool = True,
        as_of: datetime | None = None,
    ) -> ProjectMemory | None:
        raise NotImplementedError

    def decide_proposal(
        self,
        proposal_id: UUID,
        *,
        approved: bool,
        decided_by: str,
    ) -> tuple[MemoryProposal, ProjectMemory | None]:
        raise NotImplementedError

    def list_memories(
        self,
        project: ProjectRef,
        *,
        include_inactive: bool = False,
        as_of: datetime | None = None,
        fact_key: str | None = None,
    ) -> tuple[ProjectMemory, ...]:
        raise NotImplementedError

    def search_memories(
        self,
        project: ProjectRef,
        query: str,
        *,
        memory_types=(),
        subject: str | None = None,
        as_of: datetime | None = None,
        limit: int = 128,
    ) -> tuple[ProjectMemory, ...]:
        """Return a bounded candidate set; implementations may use an index."""

        del query, limit
        return self.list_memories(project)

    def list_memory_versions(self, project: ProjectRef, fact_key: str) -> tuple[ProjectMemory, ...]:
        return tuple(sorted(
            (item for item in self.list_memories(project, include_inactive=True) if item.fact_key == fact_key),
            key=lambda item: (item.valid_from, str(item.id)),
        ))

    def mark_needs_review(self, memory_id: UUID, *, reason: ReviewReason) -> ProjectMemory:
        raise NotImplementedError

    def revoke_memory(self, memory_id: UUID, *, revoked_by: str, reason: str, at: datetime | None = None) -> ProjectMemory:
        raise NotImplementedError

    def approve_replacement(self, proposal_id: UUID, *, decided_by: str):
        updated, memory = self.decide_proposal(proposal_id, approved=True, decided_by=decided_by)
        old = None
        if memory is not None and memory.supersedes_memory_id is not None:
            old = self.get_memory(memory.supersedes_memory_id)
        return updated, memory, old

    def get_memory_summary(self, project: ProjectRef):
        """Return a cached navigational summary when the store supports one."""

        del project
        return None


def _validate_new_proposal(proposal: MemoryProposal) -> None:
    if not proposal.evidence_ids:
        raise ValueError("memory proposal requires evidence_ids")
    if proposal.status != "pending":
        raise ValueError("new memory proposal must be pending")
    for source in proposal.source_refs:
        if source.tenant_id != proposal.project.tenant_id or source.project_id != proposal.project.project_id:
            raise ValueError("MEMORY_INVALID_PROVENANCE: source reference is outside the proposal project")


def _assert_replace_target(
    proposal: MemoryProposal,
    *,
    get_memory,
    list_memories,
) -> None:
    if proposal.replaces_memory_id is None:
        return
    target = get_memory(proposal.replaces_memory_id)
    if target is None:
        raise ValueError("replaces_memory_id does not reference an existing memory")
    if (
        target.project.tenant_id != proposal.project.tenant_id
        or target.project.project_id != proposal.project.project_id
    ):
        raise ValueError("replaces_memory_id must belong to the same project")
    active = {item.id for item in list_memories(proposal.project)}
    if target.id not in active:
        raise ValueError("replaces_memory_id is not an active project memory")


def _assert_no_unresolved_conflict(
    proposal: MemoryProposal,
    *,
    list_memories,
) -> None:
    proposed_fact_key = build_fact_key(
        project=proposal.project,
        memory_type=proposal.memory_type,
        subject=proposal.subject,
        claim_text=proposal.claim_text,
        claim_slot=proposal.claim_slot,
    )
    for item in list_memories(proposal.project):
        if item.memory_type != proposal.memory_type:
            continue
        same_fact = proposed_fact_key is not None and item.fact_key == proposed_fact_key
        same_text = normalize_memory_text(item.text) == normalize_memory_text(proposal.claim_text)
        if not same_fact and not same_text:
            continue
        if proposal.replaces_memory_id == item.id:
            return
        raise ValueError(
            "conflicting active project memory exists; "
            "set replaces_memory_id to replace it explicitly"
        )


def _apply_decision(
    current: MemoryProposal,
    *,
    approved: bool,
    decided_by: str,
) -> tuple[MemoryProposal, ProjectMemory | None]:
    try:
        updated = prepare_decision(
            current,
            approved=approved,
            decided_by=decided_by,
        )
    except ApprovalError as exc:
        # Preserve expired transition when prepare detects expiry.
        refreshed = refresh_expiration(current)
        if refreshed.status == "expired" and current.status == "pending":
            raise ApprovalError("memory proposal has expired") from exc
        raise
    if not approved:
        return updated, None
    content_hash = current.content_hash or _memory_content_hash(current)
    memory = ProjectMemory(
        project=current.project,
        text=current.claim_text,
        memory_type=current.memory_type,
        claim_type=current.claim_type,
        fact_key=build_fact_key(
            project=current.project,
            memory_type=current.memory_type,
            subject=current.subject,
            claim_text=current.claim_text,
            claim_slot=current.claim_slot,
        ),
        subject=current.subject,
        evidence_ids=current.evidence_ids,
        source_refs=current.source_refs,
        authority_scope=current.authority_scope,
        visibility_scope=current.visibility_scope,
        approved_by=decided_by,
        valid_from=current.valid_from or datetime.now(timezone.utc),
        observed_at=current.observed_at,
        review_due_at=current.review_due_at,
        content_hash=content_hash,
        supersedes_memory_id=current.replaces_memory_id,
        proposal_id=current.id,
    )
    return updated, memory


def _memory_content_hash(proposal: MemoryProposal) -> str:
    canonical = "|".join(
        (
            normalize_memory_text(proposal.claim_text),
            proposal.memory_type.value,
            proposal.subject or "",
            *(f"{ref.tenant_id}:{ref.project_id}:{ref.source_id}:{ref.revision}:{ref.content_hash or ''}" for ref in proposal.source_refs),
            *(str(item) for item in proposal.evidence_ids),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _revoke_memory(memory: ProjectMemory, *, at: datetime) -> ProjectMemory:
    return memory.model_copy(update={"valid_to": at, "status": MemoryStatus.SUPERSEDED, "updated_at": at})


def _memory_is_current(memory: ProjectMemory, *, as_of: datetime | None = None) -> bool:
    moment = as_of or datetime.now(timezone.utc)
    valid_from = memory.valid_from if memory.valid_from.tzinfo else memory.valid_from.replace(tzinfo=timezone.utc)
    valid_to = memory.valid_to
    if valid_to is not None and valid_to.tzinfo is None:
        valid_to = valid_to.replace(tzinfo=timezone.utc)
    return (
        memory.status is MemoryStatus.ACTIVE
        and valid_from <= moment
        and (valid_to is None or valid_to > moment)
    )


class InMemoryMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._proposals: dict[UUID, MemoryProposal] = {}
        self._memories: dict[UUID, ProjectMemory] = {}

    def create_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
        existing = self._proposals.get(proposal.id)
        if existing is not None:
            return existing
        _validate_new_proposal(proposal)
        _assert_replace_target(
            proposal,
            get_memory=self.get_memory,
            list_memories=self.list_memories,
        )
        _assert_no_unresolved_conflict(proposal, list_memories=self.list_memories)
        self._proposals[proposal.id] = proposal
        return proposal

    def get_proposal(self, proposal_id: UUID) -> MemoryProposal | None:
        current = self._proposals.get(proposal_id)
        if current is None:
            return None
        refreshed = refresh_expiration(current)
        if refreshed is not current:
            self._proposals[proposal_id] = refreshed
        return refreshed

    def get_memory(
        self,
        memory_id: UUID,
        *,
        project: ProjectRef | None = None,
        include_inactive: bool = True,
        as_of: datetime | None = None,
    ) -> ProjectMemory | None:
        memory = self._memories.get(memory_id)
        if memory is None:
            return None
        if project is not None and (
            memory.project.tenant_id != project.tenant_id
            or memory.project.project_id != project.project_id
        ):
            return None
        if not include_inactive and not _memory_is_current(memory, as_of=as_of):
            return None
        return memory

    def decide_proposal(
        self,
        proposal_id: UUID,
        *,
        approved: bool,
        decided_by: str,
    ) -> tuple[MemoryProposal, ProjectMemory | None]:
        current = self.get_proposal(proposal_id)
        if current is None:
            raise KeyError(proposal_id)
        if current.status == "expired":
            raise ApprovalError("memory proposal has expired")
        try:
            updated, memory = _apply_decision(
                current,
                approved=approved,
                decided_by=decided_by,
            )
        except ApprovalError:
            # Persist lazy expiry if prepare failed due to expiry race.
            refreshed = refresh_expiration(current)
            if refreshed.status == "expired":
                self._proposals[proposal_id] = refreshed
            raise
        except PermissionError:
            raise
        self._proposals[proposal_id] = updated
        if memory is not None:
            if current.replaces_memory_id is not None:
                old = self._memories.get(current.replaces_memory_id)
                if old is not None:
                    self._memories[old.id] = _revoke_memory(old, at=memory.valid_from)
            self._memories[memory.id] = memory
        return updated, memory

    def list_memories(
        self,
        project: ProjectRef,
        *,
        include_inactive: bool = False,
        as_of: datetime | None = None,
        fact_key: str | None = None,
    ) -> tuple[ProjectMemory, ...]:
        return tuple(
            item
            for item in self._memories.values()
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
            and (fact_key is None or item.fact_key == fact_key)
            and (include_inactive or _memory_is_current(item, as_of=as_of))
        )

    def search_memories(
        self,
        project: ProjectRef,
        query: str,
        *,
        memory_types=(),
        subject: str | None = None,
        as_of: datetime | None = None,
        limit: int = 128,
    ) -> tuple[ProjectMemory, ...]:
        # Keep the deterministic in-memory implementation simple; ranking remains
        # centralized in memory_retrieval.py.
        del query
        memories = self.list_memories(project, as_of=as_of)
        if memory_types:
            memories = tuple(item for item in memories if item.memory_type in memory_types)
        if subject is not None:
            memories = tuple(item for item in memories if item.subject == subject)
        return memories[: min(128, max(1, int(limit)))]

    def mark_needs_review(self, memory_id: UUID, *, reason: ReviewReason) -> ProjectMemory:
        memory = self.get_memory(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        updated = memory.model_copy(update={"status": MemoryStatus.NEEDS_REVIEW, "updated_at": datetime.now(timezone.utc)})
        self._memories[memory_id] = updated
        return updated

    def revoke_memory(self, memory_id: UUID, *, revoked_by: str, reason: str, at: datetime | None = None) -> ProjectMemory:
        del revoked_by, reason
        memory = self.get_memory(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        when = at or datetime.now(timezone.utc)
        updated = memory.model_copy(update={"status": MemoryStatus.REVOKED, "valid_to": when, "updated_at": when})
        self._memories[memory_id] = updated
        return updated

    def get_memory_summary(self, project: ProjectRef):
        from project_lens.context.memory_retrieval import build_memory_summary

        return build_memory_summary(self.list_memories(project))


class SQLiteMemoryStore(MemoryStore):
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_proposals (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_summaries (
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (tenant_id, project_id)
            )
            """
        )
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS project_memories (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )
        self._database.run_migrations(
            "memory",
            {
                1: lambda connection: None,
                2: _migrate_memory_indexes,
                3: _create_review_schema,
                4: _create_memory_audit_schema,
            },
        )
        self._fts_enabled = True
        try:
            self._database.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS project_memories_fts USING fts5(
                    memory_id UNINDEXED,
                    tenant_id UNINDEXED,
                    project_id UNINDEXED,
                    search_text
                )
                """
            )
        except Exception:
            # SQLite builds without FTS5 retain the existing bounded fallback.
            self._fts_enabled = False
        if self._fts_enabled:
            self._backfill_fts()
        self._backfill_summaries()

    def create_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
        existing = self.get_proposal(proposal.id)
        if existing is not None:
            return existing
        _validate_new_proposal(proposal)
        _assert_replace_target(
            proposal,
            get_memory=self.get_memory,
            list_memories=self.list_memories,
        )
        _assert_no_unresolved_conflict(proposal, list_memories=self.list_memories)
        self._database.execute(
            "INSERT OR REPLACE INTO memory_proposals (id, payload) VALUES (?, ?)",
            (str(proposal.id), proposal.model_dump_json()),
        )
        return proposal

    def get_proposal(self, proposal_id: UUID) -> MemoryProposal | None:
        row = self._database.query_one(
            "SELECT payload FROM memory_proposals WHERE id = ?",
            (str(proposal_id),),
        )
        if row is None:
            return None
        current = MemoryProposal.model_validate_json(row["payload"])
        refreshed = refresh_expiration(current)
        if refreshed is not current:
            self._database.execute(
                "INSERT OR REPLACE INTO memory_proposals (id, payload) VALUES (?, ?)",
                (str(refreshed.id), refreshed.model_dump_json()),
            )
        return refreshed

    def get_memory(
        self,
        memory_id: UUID,
        *,
        project: ProjectRef | None = None,
        include_inactive: bool = True,
        as_of: datetime | None = None,
    ) -> ProjectMemory | None:
        row = self._database.query_one(
            "SELECT payload FROM project_memories WHERE id = ?",
            (str(memory_id),),
        )
        if row is None:
            return None
        memory = ProjectMemory.model_validate_json(row["payload"])
        if project is not None and (
            memory.project.tenant_id != project.tenant_id
            or memory.project.project_id != project.project_id
        ):
            return None
        if not include_inactive and not _memory_is_current(memory, as_of=as_of):
            return None
        return memory

    def decide_proposal(
        self,
        proposal_id: UUID,
        *,
        approved: bool,
        decided_by: str,
    ) -> tuple[MemoryProposal, ProjectMemory | None]:
        current = self.get_proposal(proposal_id)
        if current is None:
            raise KeyError(proposal_id)
        if current.status == "expired":
            raise ApprovalError("memory proposal has expired")
        try:
            updated, memory = _apply_decision(
                current,
                approved=approved,
                decided_by=decided_by,
            )
        except ApprovalError:
            refreshed = refresh_expiration(current)
            if refreshed.status == "expired":
                self._database.execute(
                    "INSERT OR REPLACE INTO memory_proposals (id, payload) VALUES (?, ?)",
                    (str(refreshed.id), refreshed.model_dump_json()),
                )
            raise
        self._database.execute(
            "INSERT OR REPLACE INTO memory_proposals (id, payload) VALUES (?, ?)",
            (str(updated.id), updated.model_dump_json()),
        )
        if memory is None:
            return updated, None
        if current.replaces_memory_id is not None:
            old = self.get_memory(current.replaces_memory_id)
            if old is not None:
                revoked = _revoke_memory(old, at=memory.valid_from)
                self._database.execute(
                    """INSERT OR REPLACE INTO project_memories
                       (id, payload, tenant_id, project_id, fact_key, memory_status, valid_from, valid_to, content_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(revoked.id), revoked.model_dump_json(), revoked.project.tenant_id, revoked.project.project_id,
                     revoked.fact_key, revoked.status.value, revoked.valid_from.isoformat(), revoked.valid_to.isoformat() if revoked.valid_to else None, revoked.content_hash),
                )
        self._database.execute(
            """INSERT OR REPLACE INTO project_memories
               (id, payload, tenant_id, project_id, fact_key, memory_status, valid_from, valid_to, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(memory.id), memory.model_dump_json(), memory.project.tenant_id, memory.project.project_id,
             memory.fact_key, memory.status.value, memory.valid_from.isoformat(), memory.valid_to.isoformat() if memory.valid_to else None, memory.content_hash),
        )
        self._index_memory(memory)
        self._refresh_summary(memory.project)
        return updated, memory

    def approve_replacement(self, proposal_id: UUID, *, decided_by: str):
        """Approve from a freshly read proposal and commit the version chain atomically."""

        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload FROM memory_proposals WHERE id = ?", (str(proposal_id),)
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            current = refresh_expiration(MemoryProposal.model_validate_json(row["payload"]))
            if current.status == "expired":
                connection.execute(
                    "UPDATE memory_proposals SET payload = ? WHERE id = ?",
                    (current.model_dump_json(), str(proposal_id)),
                )
                raise ApprovalError("memory proposal has expired")
            project_rows = connection.execute(
                "SELECT payload FROM project_memories WHERE tenant_id = ? AND project_id = ?",
                (current.project.tenant_id, current.project.project_id),
            ).fetchall()
            project_memories = []
            for project_row in project_rows:
                try:
                    project_memories.append(ProjectMemory.model_validate_json(project_row["payload"]))
                except (TypeError, ValueError):
                    continue
            if current.replaces_memory_id is not None:
                target = next((item for item in project_memories if item.id == current.replaces_memory_id), None)
                if target is None or not _memory_is_current(target):
                    raise ValueError("replaces_memory_id is not an active project memory")
            _assert_no_unresolved_conflict(current, list_memories=lambda _project: tuple(item for item in project_memories if _memory_is_current(item)))
            updated, memory = _apply_decision(current, approved=True, decided_by=decided_by)
            old = None
            if memory is not None and current.replaces_memory_id is not None:
                old = next((item for item in project_memories if item.id == current.replaces_memory_id), None)
                if old is not None:
                    old = _revoke_memory(old, at=memory.valid_from)
                    connection.execute(
                        "UPDATE project_memories SET payload = ?, memory_status = ?, valid_to = ? WHERE id = ?",
                        (old.model_dump_json(), old.status.value, old.valid_to.isoformat() if old.valid_to else None, str(old.id)),
                    )
            connection.execute(
                "UPDATE memory_proposals SET payload = ? WHERE id = ?",
                (updated.model_dump_json(), str(proposal_id)),
            )
            if memory is not None:
                connection.execute(
                    """INSERT OR REPLACE INTO project_memories
                    (id, payload, tenant_id, project_id, fact_key, memory_status, valid_from, valid_to, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(memory.id), memory.model_dump_json(), memory.project.tenant_id, memory.project.project_id,
                     memory.fact_key, memory.status.value, memory.valid_from.isoformat(), memory.valid_to.isoformat() if memory.valid_to else None, memory.content_hash),
                )
        if memory is not None:
            self._index_memory(memory)
            self._refresh_summary(memory.project)
        return updated, memory, old

    def list_memories(
        self,
        project: ProjectRef,
        *,
        include_inactive: bool = False,
        as_of: datetime | None = None,
        fact_key: str | None = None,
    ) -> tuple[ProjectMemory, ...]:
        rows = self._database.query_all(
            "SELECT payload FROM project_memories WHERE tenant_id = ? AND project_id = ?",
            (project.tenant_id, project.project_id),
        )
        memories = []
        for row in rows:
            try:
                memories.append(ProjectMemory.model_validate_json(row["payload"]))
            except (TypeError, ValueError):
                continue
        return tuple(
            item
            for item in memories
            if (fact_key is None or item.fact_key == fact_key)
            and (include_inactive or _memory_is_current(item, as_of=as_of))
        )

    def search_memories(
        self,
        project: ProjectRef,
        query: str,
        *,
        memory_types=(),
        subject: str | None = None,
        as_of: datetime | None = None,
        limit: int = 128,
    ) -> tuple[ProjectMemory, ...]:
        active = {item.id: item for item in self.list_memories(project, as_of=as_of)}
        if memory_types:
            active = {key: item for key, item in active.items() if item.memory_type in memory_types}
        if subject is not None:
            active = {key: item for key, item in active.items() if item.subject == subject}
        if not self._fts_enabled or not query.strip():
            return tuple(active.values())[: max(1, int(limit))]
        tokens = _search_tokens(query)
        if not tokens:
            return tuple(active.values())[: max(1, int(limit))]
        match = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens[:16])
        try:
            rows = self._database.query_all(
                "SELECT memory_id FROM project_memories_fts "
                "WHERE tenant_id = ? AND project_id = ? AND project_memories_fts MATCH ? "
                "ORDER BY bm25(project_memories_fts) LIMIT ?",
                (project.tenant_id, project.project_id, match, max(1, int(limit))),
            )
        except Exception:
            return tuple(active.values())[: max(1, int(limit))]
        indexed = [active[UUID(row["memory_id"])] for row in rows if _uuid_in(row["memory_id"], active)]
        return tuple(indexed) or tuple(active.values())[: max(1, int(limit))]

    def mark_needs_review(self, memory_id: UUID, *, reason: ReviewReason) -> ProjectMemory:
        memory = self.get_memory(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        updated = memory.model_copy(update={"status": MemoryStatus.NEEDS_REVIEW, "updated_at": datetime.now(timezone.utc)})
        self._database.execute("UPDATE project_memories SET payload = ?, memory_status = ? WHERE id = ?", (updated.model_dump_json(), updated.status.value, str(memory_id)))
        self._index_memory(updated)
        return updated

    def revoke_memory(self, memory_id: UUID, *, revoked_by: str, reason: str, at: datetime | None = None) -> ProjectMemory:
        del revoked_by, reason
        memory = self.get_memory(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        when = at or datetime.now(timezone.utc)
        updated = memory.model_copy(update={"status": MemoryStatus.REVOKED, "valid_to": when, "updated_at": when})
        self._database.execute("UPDATE project_memories SET payload = ?, memory_status = ?, valid_to = ? WHERE id = ?", (updated.model_dump_json(), updated.status.value, when.isoformat(), str(memory_id)))
        self._index_memory(updated)
        return updated

    def get_memory_summary(self, project: ProjectRef):
        from project_lens.context.memory_retrieval import MemorySummaryEntry

        row = self._database.query_one(
            "SELECT payload FROM memory_summaries WHERE tenant_id = ? AND project_id = ?",
            (project.tenant_id, project.project_id),
        )
        if row is None:
            self._refresh_summary(project)
            row = self._database.query_one(
                "SELECT payload FROM memory_summaries WHERE tenant_id = ? AND project_id = ?",
                (project.tenant_id, project.project_id),
            )
        if row is None:
            return ()
        try:
            payload = json.loads(row["payload"])
            return tuple(MemorySummaryEntry(**item) for item in payload if isinstance(item, dict))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()

    def _index_memory(self, memory: ProjectMemory) -> None:
        if not self._fts_enabled:
            return
        self._database.execute(
            "DELETE FROM project_memories_fts WHERE memory_id = ?",
            (str(memory.id),),
        )
        self._database.execute(
            "INSERT INTO project_memories_fts (memory_id, tenant_id, project_id, search_text) VALUES (?, ?, ?, ?)",
            (str(memory.id), memory.project.tenant_id, memory.project.project_id, _memory_search_text(memory)),
        )

    def _backfill_fts(self) -> None:
        rows = self._database.query_all("SELECT payload FROM project_memories")
        for row in rows:
            try:
                self._index_memory(ProjectMemory.model_validate_json(row["payload"]))
            except (TypeError, ValueError):
                continue

    def _refresh_summary(self, project: ProjectRef) -> None:
        from project_lens.context.memory_retrieval import build_memory_summary

        summary = build_memory_summary(self.list_memories(project))
        self._database.execute(
            "INSERT OR REPLACE INTO memory_summaries (tenant_id, project_id, payload) VALUES (?, ?, ?)",
            (
                project.tenant_id,
                project.project_id,
                json.dumps([entry.__dict__ for entry in summary], ensure_ascii=False),
            ),
        )

    def _backfill_summaries(self) -> None:
        rows = self._database.query_all("SELECT payload FROM project_memories")
        projects: set[tuple[str, str]] = set()
        for row in rows:
            try:
                memory = ProjectMemory.model_validate_json(row["payload"])
            except (TypeError, ValueError):
                continue
            projects.add((memory.project.tenant_id, memory.project.project_id))
        for tenant_id, project_id in projects:
            self._refresh_summary(ProjectRef(tenant_id=tenant_id, project_id=project_id))


def _memory_search_text(memory: ProjectMemory) -> str:
    return " ".join(
        (
            memory.text,
            memory.memory_type.value,
            memory.subject or "",
            memory.fact_key or "",
            *(f"{item.source_id}:{item.revision}" for item in memory.source_refs),
            *(str(item) for item in memory.evidence_ids),
            memory.project.service or "",
            memory.project.environment or "",
        )
    )


def _migrate_memory_indexes(connection) -> None:
    """Add indexed memory columns and backfill safely from legacy payloads."""

    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(project_memories)").fetchall()}
    additions = {
        "tenant_id": "TEXT",
        "project_id": "TEXT",
        "fact_key": "TEXT",
        "memory_status": "TEXT",
        "valid_from": "TEXT",
        "valid_to": "TEXT",
        "content_hash": "TEXT",
    }
    for name, kind in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE project_memories ADD COLUMN {name} {kind}")
    rows = connection.execute("SELECT id, payload FROM project_memories").fetchall()
    for row in rows:
        try:
            memory = ProjectMemory.model_validate_json(row["payload"])
        except Exception:
            continue
        connection.execute(
            """UPDATE project_memories SET tenant_id = ?, project_id = ?, fact_key = ?,
               memory_status = ?, valid_from = ?, valid_to = ?, content_hash = ? WHERE id = ?""",
            (
                memory.project.tenant_id,
                memory.project.project_id,
                memory.fact_key,
                memory.status.value,
                memory.valid_from.isoformat(),
                memory.valid_to.isoformat() if memory.valid_to else None,
                memory.content_hash,
                row["id"],
            ),
        )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_project_memories_scope ON project_memories (tenant_id, project_id, memory_status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_project_memories_fact_key ON project_memories (tenant_id, project_id, fact_key)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_project_memories_validity ON project_memories (tenant_id, project_id, valid_from, valid_to)")


def _create_review_schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS memory_reviews (
            review_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
            reason TEXT NOT NULL, status TEXT NOT NULL,
            trigger_key TEXT NOT NULL, opened_at TEXT NOT NULL,
            due_at TEXT, resolved_at TEXT, resolved_by TEXT,
            resolution TEXT, payload TEXT NOT NULL)"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_reviews_project_status "
        "ON memory_reviews (tenant_id, project_id, status, due_at)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_reviews_trigger "
        "ON memory_reviews (memory_id, reason, trigger_key)"
    )


def _create_memory_audit_schema(connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS memory_audit_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE, tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL, memory_id TEXT,
            proposal_id TEXT, run_id TEXT, event_type TEXT NOT NULL,
            actor_id TEXT, occurred_at TEXT NOT NULL,
            payload TEXT NOT NULL)"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_audit_project "
        "ON memory_audit_events (tenant_id, project_id, occurred_at)"
    )


def _search_tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9_./:-]+", text.casefold())


def _uuid_in(value: object, active: dict[UUID, ProjectMemory]) -> bool:
    try:
        return UUID(str(value)) in active
    except (TypeError, ValueError, AttributeError):
        return False
