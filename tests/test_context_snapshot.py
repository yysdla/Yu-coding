from datetime import datetime, timezone
import pytest

from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.context.source_store import InMemorySourceRecordStore
from project_lens.domain.memory import MemorySourceRef, MemoryStatus, ProjectMemory
from project_lens.domain.models import ProjectRef
from project_lens.project_space.policies import (
    AnswerDepth,
    EffectiveAccessScope,
    RoleKind,
    VisibilityLevel,
)
from project_lens.workflow.context_snapshot import (
    ContextChangedError,
    ContextSnapshotService,
    candidate_from_memory,
)


def _scope(project: ProjectRef) -> EffectiveAccessScope:
    return EffectiveAccessScope(
        project=project,
        actor_id="u1",
        chat_id="chat-1",
        role=RoleKind.DEVELOPER,
        readable_sources=(),
        allowed_tools=(),
        forbidden_sources=(),
        answer_depth=AnswerDepth.BALANCED,
        answer_style="concise",
        visibility_level=VisibilityLevel.TEAM_SHARED,
        chat_type="group",
    )


def _memory(project: ProjectRef, text: str) -> ProjectMemory:
    return ProjectMemory(
        project=project,
        text=text,
        approved_by="approver",
        evidence_ids=(),
        status=MemoryStatus.ACTIVE,
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_freeze_and_replay_keeps_reference_metadata_and_text() -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    store = InMemoryMemoryStore()
    memory = _memory(project, "owner is Alice")
    store._memories[memory.id] = memory
    service = ContextSnapshotService(memory_store=store)

    frozen = service.freeze_and_start(
        project=project,
        scope=_scope(project),
        candidates=(candidate_from_memory(memory),),
    )

    assert frozen.snapshot.included_memory_ids == (memory.id,)
    assert frozen.snapshot.records[0].text == "owner is Alice"
    replayed = service.replay(frozen.snapshot.snapshot_id, project=project, scope=_scope(project))
    assert replayed.memories[0].id == memory.id
    assert replayed.snapshot.context_hash == frozen.snapshot.context_hash


def test_replay_hash_mismatch_is_explicit_context_changed() -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    store = InMemoryMemoryStore()
    memory = _memory(project, "owner is Alice")
    store._memories[memory.id] = memory
    service = ContextSnapshotService(memory_store=store)
    frozen = service.freeze_and_start(
        project=project,
        scope=_scope(project),
        candidates=(candidate_from_memory(memory),),
    )
    store._memories[memory.id] = memory.model_copy(update={"text": "owner is Bob"})

    with pytest.raises(ContextChangedError) as exc:
        service.replay(frozen.snapshot.snapshot_id, project=project, scope=_scope(project))
    assert exc.value.code == "CONTEXT_CHANGED"


def test_freeze_hash_mismatch_persists_changed_audit_record() -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    store = InMemoryMemoryStore()
    memory = _memory(project, "owner is Alice")
    store._memories[memory.id] = memory
    candidate = candidate_from_memory(memory).model_copy(update={"source_hash": "f" * 64})
    service = ContextSnapshotService(memory_store=store)

    with pytest.raises(ContextChangedError) as exc:
        service.freeze_and_start(
            project=project,
            scope=_scope(project),
            candidates=(candidate,),
        )

    assert exc.value.snapshot is not None
    assert exc.value.snapshot.records[0].state == "changed"
    assert exc.value.snapshot.records[0].reason_code == "CONTEXT_CHANGED"


def test_freeze_rejects_revoked_or_changed_source_reference() -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    source_store = InMemorySourceRecordStore()
    source_store.put(
        SourceRecord(
            source_id="doc-1",
            source_type=SourceType.FEISHU_DOCUMENT,
            tenant_id="t1",
            project_id="p1",
            revision="r1",
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            access_scope="docs/",
            content_hash="a" * 16,
            content="source",
        )
    )
    store = InMemoryMemoryStore()
    memory = _memory(project, "owner is Alice").model_copy(
        update={
            "source_refs": (
                MemorySourceRef(
                    tenant_id="t1",
                    project_id="p1",
                    source_id="doc-1",
                    revision="r1",
                    content_hash="b" * 16,
                    source_type="feishu_document",
                ),
            ),
        }
    )
    store._memories[memory.id] = memory
    service = ContextSnapshotService(memory_store=store, source_store=source_store)

    with pytest.raises(RuntimeError, match="MEMORY_INVALID_PROVENANCE"):
        service.freeze_and_start(
            project=project,
            scope=_scope(project),
            candidates=(candidate_from_memory(memory),),
        )
