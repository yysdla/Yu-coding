from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from project_lens.context.knowledge_index import (
    EpisodeAdapter,
    EvidenceAdapter,
    KnowledgeObjectKind,
    ProjectMemoryAdapter,
    SourceRecordAdapter,
    stable_document_id,
)
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.domain.memory import Episode, MemoryStatus, ProjectMemory
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef


def test_evidence_projection_is_stable_and_round_trips() -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="docs", source_id="doc-1"),
        content="支付回调规则",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:p1:read",
        content_hash="a" * 64,
    )
    first = EvidenceAdapter().adapt(evidence)
    second = EvidenceAdapter().adapt(evidence)

    assert first.id == second.id
    assert first.kind is KnowledgeObjectKind.EVIDENCE
    assert first.content_hash == second.content_hash
    assert first == first.model_validate_json(first.model_dump_json())


def test_source_revision_is_part_of_document_identity() -> None:
    now = datetime.now(timezone.utc)
    base = dict(
        source_id="req-1",
        source_type=SourceType.FEISHU_DOCUMENT,
        project_id="p1",
        tenant_id="t1",
        observed_at=now,
        access_scope="project:p1:read",
        content_hash="a" * 64,
        content="需求一",
    )
    v1 = SourceRecord(revision="1", **base)
    v2 = SourceRecord(
        source_id="req-1",
        source_type=SourceType.FEISHU_DOCUMENT,
        project_id="p1",
        tenant_id="t1",
        observed_at=now,
        access_scope="project:p1:read",
        content_hash="b" * 64,
        content="需求二",
        revision="2",
    )
    assert SourceRecordAdapter().adapt(v1).id != SourceRecordAdapter().adapt(v2).id
    assert v1.key != v2.key


def test_revoked_and_expired_memory_is_not_indexable() -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    adapter = ProjectMemoryAdapter()
    revoked = ProjectMemory(
        project=project,
        text="旧事实",
        approved_by="owner",
        status=MemoryStatus.REVOKED,
    )
    expired = ProjectMemory(
        project=project,
        text="过期事实",
        approved_by="owner",
        valid_to=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert adapter.adapt(revoked).indexable is False
    assert adapter.adapt(expired).indexable is False


def test_episode_projection_is_historical_and_project_scoped() -> None:
    project = ProjectRef(tenant_id="t1", project_id="p1")
    now = datetime.now(timezone.utc)
    episode = Episode(
        project=project,
        run_id=uuid4(),
        title="一次排障",
        summary="定位到支付回调",
        started_at=now,
        ended_at=now,
        status="completed",
    )
    document = EpisodeAdapter().adapt(episode)
    assert document.metadata["historical"] is True
    assert document.project_id == "p1"


def test_document_id_is_deterministic() -> None:
    args = ("t1", "p1", "evidence", "obj-1", "r1")
    assert stable_document_id(*args) == stable_document_id(*args)
