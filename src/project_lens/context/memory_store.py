"""In-process and SQLite-backed project memory governance."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from project_lens.application.approval_harness import (
    ApprovalError,
    prepare_decision,
    refresh_expiration,
)
from project_lens.domain.memory import MemoryProposal, ProjectMemory, normalize_memory_text
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


class MemoryStore:
    """Pending proposals and approved project memories."""

    def create_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
        raise NotImplementedError

    def get_proposal(self, proposal_id: UUID) -> MemoryProposal | None:
        raise NotImplementedError

    def get_memory(self, memory_id: UUID) -> ProjectMemory | None:
        raise NotImplementedError

    def decide_proposal(
        self,
        proposal_id: UUID,
        *,
        approved: bool,
        decided_by: str,
    ) -> tuple[MemoryProposal, ProjectMemory | None]:
        raise NotImplementedError

    def list_memories(self, project: ProjectRef) -> tuple[ProjectMemory, ...]:
        raise NotImplementedError

    def search_memories(
        self,
        project: ProjectRef,
        query: str,
        *,
        limit: int = 128,
    ) -> tuple[ProjectMemory, ...]:
        """Return a bounded candidate set; implementations may use an index."""

        del query, limit
        return self.list_memories(project)

    def get_memory_summary(self, project: ProjectRef):
        """Return a cached navigational summary when the store supports one."""

        del project
        return None


def _validate_new_proposal(proposal: MemoryProposal) -> None:
    if not proposal.evidence_ids:
        raise ValueError("memory proposal requires evidence_ids")
    if proposal.status != "pending":
        raise ValueError("new memory proposal must be pending")


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
    for item in list_memories(proposal.project):
        if item.memory_type != proposal.memory_type:
            continue
        if normalize_memory_text(item.text) != normalize_memory_text(proposal.claim_text):
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
    memory = ProjectMemory(
        project=current.project,
        text=current.claim_text,
        memory_type=current.memory_type,
        evidence_ids=current.evidence_ids,
        approved_by=decided_by,
        valid_from=datetime.now(timezone.utc),
        proposal_id=current.id,
    )
    return updated, memory


def _revoke_memory(memory: ProjectMemory, *, at: datetime) -> ProjectMemory:
    return memory.model_copy(update={"valid_to": at})


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

    def get_memory(self, memory_id: UUID) -> ProjectMemory | None:
        return self._memories.get(memory_id)

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

    def list_memories(self, project: ProjectRef) -> tuple[ProjectMemory, ...]:
        now = datetime.now(timezone.utc)
        return tuple(
            item
            for item in self._memories.values()
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
            and (item.valid_to is None or item.valid_to > now)
        )

    def search_memories(
        self,
        project: ProjectRef,
        query: str,
        *,
        limit: int = 128,
    ) -> tuple[ProjectMemory, ...]:
        # Keep the deterministic in-memory implementation simple; ranking remains
        # centralized in memory_retrieval.py.
        del query
        return self.list_memories(project)[: max(1, int(limit))]

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

    def get_memory(self, memory_id: UUID) -> ProjectMemory | None:
        row = self._database.query_one(
            "SELECT payload FROM project_memories WHERE id = ?",
            (str(memory_id),),
        )
        if row is None:
            return None
        return ProjectMemory.model_validate_json(row["payload"])

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
                    "INSERT OR REPLACE INTO project_memories (id, payload) VALUES (?, ?)",
                    (str(revoked.id), revoked.model_dump_json()),
                )
        self._database.execute(
            "INSERT OR REPLACE INTO project_memories (id, payload) VALUES (?, ?)",
            (str(memory.id), memory.model_dump_json()),
        )
        self._index_memory(memory)
        self._refresh_summary(memory.project)
        return updated, memory

    def list_memories(self, project: ProjectRef) -> tuple[ProjectMemory, ...]:
        rows = self._database.query_all("SELECT payload FROM project_memories")
        now = datetime.now(timezone.utc)
        memories = [ProjectMemory.model_validate_json(row["payload"]) for row in rows]
        return tuple(
            item
            for item in memories
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
            and (item.valid_to is None or item.valid_to > now)
        )

    def search_memories(
        self,
        project: ProjectRef,
        query: str,
        *,
        limit: int = 128,
    ) -> tuple[ProjectMemory, ...]:
        active = {item.id: item for item in self.list_memories(project)}
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
            *(str(item) for item in memory.evidence_ids),
            memory.project.service or "",
            memory.project.environment or "",
        )
    )


def _search_tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9_./:-]+", text.casefold())


def _uuid_in(value: object, active: dict[UUID, ProjectMemory]) -> bool:
    try:
        return UUID(str(value)) in active
    except (TypeError, ValueError, AttributeError):
        return False
