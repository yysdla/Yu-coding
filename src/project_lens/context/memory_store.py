"""In-process and SQLite-backed project memory governance."""

from __future__ import annotations

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
            CREATE TABLE IF NOT EXISTS project_memories (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )

    def create_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
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
