"""Detail revalidation, claim checks, and replayable knowledge snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from project_lens.application.unified_knowledge_retrieval import (
    KnowledgeResult,
    SourceLayer,
)
from project_lens.context.models import AccessContext
from project_lens.context.source_store import SourceRecordStore
from project_lens.domain.models import Evidence, ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase
from project_lens.project_space.policies import EffectiveAccessScope


class KnowledgeDetailError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SnapshotRecordState(StrEnum):
    INCLUDED = "included"
    REJECTED = "rejected"
    CHANGED = "changed"


class KnowledgeDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    source_layer: SourceLayer
    project: ProjectRef
    title: str
    content: str
    content_hash: str
    evidence_ids: tuple[str, ...] = ()
    source_record_key: str | None = None
    revision: str | None = None
    status: str = "active"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    derived: bool = False
    historical: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    chunk_id: str | None = None
    kind: SourceLayer
    content_hash: str
    source_record_key: str | None = None
    evidence_ids: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    authorization_result: str
    retrieval_mode: str
    retrieval_channels: tuple[str, ...] = ()
    revision: str | None = None
    status: str = "active"
    state: SnapshotRecordState = SnapshotRecordState.INCLUDED
    reason_code: str | None = None


class KnowledgeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: UUID = Field(default_factory=uuid4)
    project: ProjectRef
    actor_id: str
    chat_id: str
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    policy_version: str
    renderer_version: str
    retrieval_mode: str
    records: tuple[KnowledgeSnapshotRecord, ...] = ()
    context_hash: str


class KnowledgeClaimVerifier:
    def verify_fact(
        self,
        *,
        claim_text: str,
        evidence_ids: tuple[str, ...],
        details: tuple[KnowledgeDetail, ...],
    ) -> bool:
        if not claim_text.strip() or not evidence_ids:
            return False
        valid = {
            evidence_id
            for detail in details
            if detail.source_layer in {SourceLayer.EVIDENCE, SourceLayer.SOURCE_RECORD}
            and detail.status.casefold() not in {"revoked", "expired"}
            for evidence_id in detail.evidence_ids
        }
        return bool(set(evidence_ids) & valid)


class KnowledgeDetailService:
    def __init__(
        self,
        *,
        context_engine,
        source_store: SourceRecordStore | None = None,
        memory_service=None,
        history_store=None,
        wiki_repository=None,
    ) -> None:
        self._context = context_engine
        self._sources = source_store
        self._memory = memory_service
        self._history = history_store
        self._wiki = wiki_repository

    def read(
        self,
        result: KnowledgeResult,
        *,
        project: ProjectRef,
        access: AccessContext,
        access_scope: EffectiveAccessScope | None = None,
        as_of: datetime | None = None,
    ) -> KnowledgeDetail:
        if project != getattr(result.payload, "project", project):
            raise KnowledgeDetailError("FORBIDDEN", "knowledge result project does not match request")
        if result.source_layer in {SourceLayer.EVIDENCE, SourceLayer.SOURCE_RECORD}:
            return self._read_evidence(result, project=project, access=access)
        if result.source_layer is SourceLayer.PROJECT_MEMORY:
            if self._memory is None or access_scope is None:
                raise KnowledgeDetailError("NOT_CONFIGURED", "memory detail service is unavailable")
            try:
                memory = self._memory.get_memory_detail(
                    memory_id=UUID(result.result_id),
                    project=project,
                    access_scope=access_scope,
                    as_of=as_of,
                )
            except PermissionError as exc:
                raise KnowledgeDetailError("FORBIDDEN", str(exc)) from exc
            return KnowledgeDetail(
                result_id=result.result_id,
                source_layer=result.source_layer,
                project=project,
                title=memory.fact_key or memory.memory_type.value,
                content=memory.text,
                content_hash=memory.content_hash or _hash(memory.text),
                evidence_ids=tuple(str(item) for item in memory.evidence_ids),
                revision=memory.updated_at.isoformat(),
                status=memory.status.value,
                valid_from=memory.valid_from,
                valid_to=memory.valid_to,
                provenance={"memory_id": result.result_id},
            )
        if result.source_layer is SourceLayer.OBSIDIAN_WIKI:
            if self._wiki is None:
                raise KnowledgeDetailError("NOT_CONFIGURED", "Wiki detail service is unavailable")
            try:
                page = self._wiki.read(project=project, relative_path=result.result_id)
            except (PermissionError, KeyError, ValueError) as exc:
                raise KnowledgeDetailError("FORBIDDEN", str(exc)) from exc
            return KnowledgeDetail(
                result_id=result.result_id,
                source_layer=result.source_layer,
                project=project,
                title=page.summary.title,
                content=page.content,
                content_hash=_hash(page.content),
                source_record_key=page.summary.source_keys[0] if len(page.summary.source_keys) == 1 else None,
                status=page.summary.status,
                derived=True,
                provenance={"path": page.summary.path, "source_keys": list(page.summary.source_keys)},
            )
        if result.source_layer is SourceLayer.EPISODE and self._history is not None:
            episode = self._history.get_episode(UUID(result.result_id))
            if episode is None or episode.project != project:
                raise KnowledgeDetailError("FORBIDDEN", "historical episode is unavailable")
            return KnowledgeDetail(
                result_id=result.result_id,
                source_layer=result.source_layer,
                project=project,
                title=episode.title,
                content=episode.summary,
                content_hash=_hash(episode.summary),
                evidence_ids=tuple(str(item) for item in episode.evidence_ids),
                revision=episode.ended_at.isoformat(),
                status=episode.status,
                historical=True,
                provenance={"run_id": str(episode.run_id)},
            )
        raise KnowledgeDetailError("NOT_FOUND", "knowledge detail is unavailable")

    def _read_evidence(
        self,
        result: KnowledgeResult,
        *,
        project: ProjectRef,
        access: AccessContext,
    ) -> KnowledgeDetail:
        authorized = self._context.authorized_evidence(project, access, limit=1_000)
        evidence = next((item for item in authorized if str(item.id) == result.result_id), None)
        if evidence is None:
            raise KnowledgeDetailError("FORBIDDEN", "evidence is no longer authorized")
        expected_hash = str((result.provenance or {}).get("content_hash") or "")
        if expected_hash and expected_hash != evidence.content_hash:
            raise KnowledgeDetailError("CONTEXT_CHANGED", "evidence content hash changed")
        source_record_key = str(
            evidence.metadata.get("source_record_key")
            or (result.provenance or {}).get("source_record_key")
            or ""
        ) or None
        if source_record_key and self._sources is not None:
            source = _source_record_for_key(self._sources, source_record_key)
            if source is None or source.revoked:
                raise KnowledgeDetailError("STALE_SOURCE", "source revision is unavailable")
            if source.content_hash != evidence.content_hash:
                raise KnowledgeDetailError("CONTEXT_CHANGED", "source content hash changed")
        return KnowledgeDetail(
            result_id=result.result_id,
            source_layer=result.source_layer,
            project=project,
            title=str(evidence.metadata.get("title") or evidence.source.source_id),
            content=evidence.content,
            content_hash=evidence.content_hash,
            evidence_ids=(str(evidence.id),),
            source_record_key=source_record_key,
            revision=str(evidence.metadata.get("revision") or "") or None,
            status=str(evidence.metadata.get("status") or "active"),
            provenance={
                "system": evidence.source.system,
                "source_id": evidence.source.source_id,
            },
        )


class KnowledgeSnapshotService:
    def __init__(
        self,
        detail_service: KnowledgeDetailService,
        *,
        database: SQLiteDatabase | None = None,
        policy_version: str = "knowledge-policy-v1",
        renderer_version: str = "knowledge-renderer-v1",
    ) -> None:
        self._detail = detail_service
        self._policy_version = policy_version
        self._renderer_version = renderer_version
        self._snapshots: dict[UUID, KnowledgeSnapshot] = {}
        self._database = database
        if database is not None:
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def freeze(
        self,
        results: tuple[KnowledgeResult, ...],
        *,
        project: ProjectRef,
        access: AccessContext,
        actor_id: str,
        chat_id: str,
        retrieval_mode: str,
        access_scope: EffectiveAccessScope | None = None,
        as_of: datetime | None = None,
    ) -> KnowledgeSnapshot:
        records: list[KnowledgeSnapshotRecord] = []
        for result in results:
            try:
                detail = self._detail.read(
                    result,
                    project=project,
                    access=access,
                    access_scope=access_scope,
                    as_of=as_of,
                )
            except KnowledgeDetailError as exc:
                records.append(
                    KnowledgeSnapshotRecord(
                        result_id=result.result_id,
                        kind=result.source_layer,
                        content_hash=str((result.provenance or {}).get("content_hash") or ""),
                        authorization_result="rejected",
                        retrieval_mode=retrieval_mode,
                        retrieval_channels=tuple(
                            result.retrieval_channels
                        ),
                        state=SnapshotRecordState.REJECTED,
                        reason_code=exc.code,
                    )
                )
                continue
            records.append(
                KnowledgeSnapshotRecord(
                    result_id=result.result_id,
                    kind=detail.source_layer,
                    content_hash=detail.content_hash,
                    source_record_key=detail.source_record_key,
                    evidence_ids=detail.evidence_ids,
                    valid_from=detail.valid_from,
                    valid_to=detail.valid_to,
                    authorization_result="authorized",
                    retrieval_mode=retrieval_mode,
                    retrieval_channels=result.retrieval_channels,
                    revision=detail.revision,
                    status=detail.status,
                )
            )
        snapshot = KnowledgeSnapshot(
            project=project,
            actor_id=actor_id,
            chat_id=chat_id,
            as_of=as_of or datetime.now(timezone.utc),
            policy_version=self._policy_version,
            renderer_version=self._renderer_version,
            retrieval_mode=retrieval_mode,
            records=tuple(records),
            context_hash=_context_hash(records),
        )
        self._put(snapshot)
        return snapshot

    def replay(
        self,
        snapshot_id: UUID,
        *,
        project: ProjectRef,
        access: AccessContext,
        actor_id: str,
        chat_id: str,
        access_scope: EffectiveAccessScope | None = None,
    ) -> tuple[KnowledgeDetail, ...]:
        snapshot = self._get(snapshot_id)
        if snapshot is None:
            raise KnowledgeDetailError("SNAPSHOT_NOT_FOUND", "knowledge snapshot does not exist")
        if snapshot.project != project or snapshot.actor_id != actor_id or snapshot.chat_id != chat_id:
            raise KnowledgeDetailError("FORBIDDEN", "knowledge snapshot is bound to another context")
        details: list[KnowledgeDetail] = []
        for record in snapshot.records:
            if record.state is not SnapshotRecordState.INCLUDED:
                continue
            result = KnowledgeResult(
                result_id=record.result_id,
                source_layer=record.kind,
                title="snapshot",
                snippet="",
                score=0.0,
                evidence_ids=record.evidence_ids,
                provenance={
                    "content_hash": record.content_hash,
                    "source_record_key": record.source_record_key,
                },
            )
            detail = self._detail.read(
                result,
                project=project,
                access=access,
                access_scope=access_scope,
                as_of=snapshot.as_of,
            )
            if detail.content_hash != record.content_hash:
                raise KnowledgeDetailError("CONTEXT_CHANGED", "snapshot content hash changed")
            if record.revision and detail.revision and record.revision != detail.revision:
                raise KnowledgeDetailError("STALE_SOURCE", "snapshot source revision changed")
            details.append(detail)
        return tuple(details)

    def _put(self, snapshot: KnowledgeSnapshot) -> None:
        self._snapshots[snapshot.snapshot_id] = snapshot
        if self._database is not None:
            self._database.execute(
                "INSERT OR REPLACE INTO knowledge_snapshots (snapshot_id, tenant_id, project_id, payload) VALUES (?, ?, ?, ?)",
                (
                    str(snapshot.snapshot_id),
                    snapshot.project.tenant_id,
                    snapshot.project.project_id,
                    snapshot.model_dump_json(),
                ),
            )

    def _get(self, snapshot_id: UUID) -> KnowledgeSnapshot | None:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is not None:
            return snapshot
        if self._database is None:
            return None
        row = self._database.query_one(
            "SELECT payload FROM knowledge_snapshots WHERE snapshot_id = ?",
            (str(snapshot_id),),
        )
        return KnowledgeSnapshot.model_validate_json(row["payload"]) if row else None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _context_hash(records: list[KnowledgeSnapshotRecord]) -> str:
    payload = json.dumps(
        [record.model_dump(mode="json") for record in records],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _hash(payload)


def _source_record_for_key(source_store: SourceRecordStore, key: str):
    parts = key.split(":", 2)
    if len(parts) != 3:
        return None
    tenant_id, project_id, source_and_revision = parts
    source_id, separator, revision = source_and_revision.rpartition(":")
    if not separator:
        return None
    return source_store.get(tenant_id, project_id, source_id, revision)


__all__ = [
    "KnowledgeClaimVerifier",
    "KnowledgeDetail",
    "KnowledgeDetailError",
    "KnowledgeDetailService",
    "KnowledgeSnapshot",
    "KnowledgeSnapshotRecord",
    "KnowledgeSnapshotService",
    "SnapshotRecordState",
]
