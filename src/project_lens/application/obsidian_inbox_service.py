"""Proposal-only import of Obsidian Inbox content."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from project_lens.context.models import AccessContext
from project_lens.context.source_records import SourceRecord
from project_lens.context.source_store import SourceRecordStore
from project_lens.domain.knowledge_proposal import KnowledgeProposal, KnowledgeProposalStatus
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.inbox import InboxCandidate, ObsidianInboxScanner
from project_lens.persistence.sqlite import SQLiteDatabase


class KnowledgeProposalStore(Protocol):
    def create_or_get(self, proposal: KnowledgeProposal) -> KnowledgeProposal: ...

    def get_by_source(self, source_path: str, source_hash: str) -> KnowledgeProposal | None: ...

    def list_for_project(self, project: ProjectRef) -> tuple[KnowledgeProposal, ...]: ...


class InMemoryKnowledgeProposalStore:
    def __init__(self) -> None:
        self._items: dict[str, KnowledgeProposal] = {}
        self._by_source: dict[tuple[str, str], str] = {}

    def create_or_get(self, proposal: KnowledgeProposal) -> KnowledgeProposal:
        existing = self.get_by_source(proposal.source_path, proposal.source_hash)
        if existing is not None:
            return existing
        current = self._items.get(proposal.id)
        if current is not None:
            return current
        self._items[proposal.id] = proposal
        self._by_source[(proposal.source_path, proposal.source_hash)] = proposal.id
        return proposal

    def get_by_source(self, source_path: str, source_hash: str) -> KnowledgeProposal | None:
        proposal_id = self._by_source.get((source_path, source_hash))
        return self._items.get(proposal_id) if proposal_id else None

    def list_for_project(self, project: ProjectRef) -> tuple[KnowledgeProposal, ...]:
        return tuple(
            item
            for item in self._items.values()
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
        )


class SQLiteKnowledgeProposalStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._db = database
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_proposals (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE (source_path, source_hash)
            )"""
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_proposals_project ON knowledge_proposals (tenant_id, project_id)"
        )

    def create_or_get(self, proposal: KnowledgeProposal) -> KnowledgeProposal:
        existing = self.get_by_source(proposal.source_path, proposal.source_hash)
        if existing is not None:
            return existing
        self._db.execute(
            "INSERT OR IGNORE INTO knowledge_proposals (id, tenant_id, project_id, source_path, source_hash, payload) VALUES (?, ?, ?, ?, ?, ?)",
            (
                proposal.id,
                proposal.project.tenant_id,
                proposal.project.project_id,
                proposal.source_path,
                proposal.source_hash,
                proposal.model_dump_json(),
            ),
        )
        return self.get_by_source(proposal.source_path, proposal.source_hash) or proposal

    def get_by_source(self, source_path: str, source_hash: str) -> KnowledgeProposal | None:
        row = self._db.query_one(
            "SELECT payload FROM knowledge_proposals WHERE source_path = ? AND source_hash = ?",
            (source_path, source_hash),
        )
        return KnowledgeProposal.model_validate_json(row["payload"]) if row else None

    def list_for_project(self, project: ProjectRef) -> tuple[KnowledgeProposal, ...]:
        rows = self._db.query_all(
            "SELECT payload FROM knowledge_proposals WHERE tenant_id = ? AND project_id = ? ORDER BY source_path",
            (project.tenant_id, project.project_id),
        )
        return tuple(KnowledgeProposal.model_validate_json(row["payload"]) for row in rows)


@dataclass(frozen=True)
class ProposalImportResult:
    project: ProjectRef
    imported: tuple[KnowledgeProposal, ...] = ()
    duplicates: tuple[KnowledgeProposal, ...] = ()
    rejected: tuple[InboxCandidate, ...] = ()


class ObsidianInboxService:
    """Convert Inbox files into proposal records, never into confirmed facts."""

    def __init__(
        self,
        *,
        scanner: ObsidianInboxScanner,
        proposal_store: KnowledgeProposalStore,
        source_store: SourceRecordStore,
    ) -> None:
        self._scanner = scanner
        self._proposals = proposal_store
        self._sources = source_store

    def import_inbox(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        created_by: str,
        relative_path: str | None = None,
    ) -> ProposalImportResult:
        if access.tenant_id != project.tenant_id:
            raise PermissionError("proposal access tenant does not match project")
        candidates = (
            (self._scanner.read_candidate(project=project, relative_path=relative_path, created_by=created_by),)
            if relative_path
            else self._scanner.scan(project=project, created_by=created_by)
        )
        imported: list[KnowledgeProposal] = []
        duplicates: list[KnowledgeProposal] = []
        rejected: list[InboxCandidate] = []
        visible_sources = tuple(
            item
            for item in self._sources.all(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
            )
            if item.access_scope in access.permissions
        )
        for candidate in candidates:
            if candidate.proposal is None:
                rejected.append(candidate)
                continue
            proposal = self._enrich(candidate.proposal, visible_sources)
            existing = self._proposals.get_by_source(proposal.source_path, proposal.source_hash)
            stored = self._proposals.create_or_get(proposal)
            self._scanner.write_review(project=project, proposal=stored)
            if existing is not None:
                duplicates.append(stored)
            else:
                imported.append(stored)
        return ProposalImportResult(
            project=project,
            imported=tuple(imported),
            duplicates=tuple(duplicates),
            rejected=tuple(rejected),
        )

    def propose_wiki_update(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        created_by: str,
        title: str,
        content: str,
        kind: str = "wiki_update",
    ) -> KnowledgeProposal:
        if access.tenant_id != project.tenant_id:
            raise PermissionError("proposal access tenant does not match project")
        if not title.strip() or not content.strip():
            raise ValueError("proposal title and content are required")
        self._scanner.validate_content(project=project, content=content)
        source_path = f"hermes://{project.tenant_id}/{project.project_id}/{kind}/{title.strip()}"
        source_hash = _hash(content)
        existing = self._proposals.get_by_source(source_path, source_hash)
        if existing is not None:
            return existing
        visible_sources = tuple(
            item
            for item in self._sources.all(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
            )
            if item.access_scope in access.permissions
        )
        proposal = KnowledgeProposal(
            project=project,
            kind=kind,
            title=title.strip(),
            content=content,
            source_path=source_path,
            source_hash=source_hash,
            status=_status_for_matches(content, visible_sources),
            evidence_ids=tuple(_source_key(item) for item in _content_matches(content, visible_sources)),
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        stored = self._proposals.create_or_get(proposal)
        self._scanner.write_review(project=project, proposal=stored)
        return stored

    def _enrich(self, proposal: KnowledgeProposal, sources: tuple[SourceRecord, ...]) -> KnowledgeProposal:
        matches = _match_explicit_evidence(proposal.evidence_ids, sources)
        if not matches:
            matches = _content_matches(proposal.content, sources)
        status = _status_for_matches(proposal.content, matches)
        return proposal.model_copy(
            update={
                "status": status,
                "evidence_ids": tuple(_source_key(item) for item in matches),
            }
        )


def _source_key(record: SourceRecord) -> str:
    return ":".join(record.key)


def _match_explicit_evidence(values: tuple[str, ...], sources: tuple[SourceRecord, ...]) -> tuple[SourceRecord, ...]:
    wanted = {value.strip() for value in values if value.strip()}
    return tuple(
        item
        for item in sources
        if _source_key(item) in wanted or item.source_id in wanted or item.content_hash in wanted
    )


def _content_matches(content: str, sources: tuple[SourceRecord, ...]) -> tuple[SourceRecord, ...]:
    tokens = _tokens(content)
    if not tokens:
        return ()
    scored = []
    for item in sources:
        haystack = _tokens(f"{item.title} {item.source_id} {item.content}")
        score = sum(1 for token in tokens if token in haystack)
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], _source_key(pair[1])))
    return tuple(item for _, item in scored[:8])


def _status_for_matches(content: str, matches: tuple[SourceRecord, ...]) -> KnowledgeProposalStatus:
    if not matches:
        return KnowledgeProposalStatus.NEEDS_EVIDENCE
    values = {_normalize(item.content) for item in matches}
    if len(values) > 1:
        return KnowledgeProposalStatus.CONFLICTED
    return KnowledgeProposalStatus.PROPOSED


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9_./:-]+|[\u4e00-\u9fff]", value.casefold()))


def _normalize(value: str) -> str:
    return "".join(value.casefold().split())


def _hash(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
