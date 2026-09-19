from __future__ import annotations

from datetime import datetime, timezone

import pytest

from project_lens.application.knowledge_snapshot import (
    KnowledgeClaimVerifier,
    KnowledgeDetailError,
    KnowledgeDetailService,
    KnowledgeSnapshotService,
)
from project_lens.application.unified_knowledge_retrieval import (
    KnowledgeResult,
    SourceLayer,
)
from project_lens.context.models import AccessContext
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.persistence.sqlite import SQLiteDatabase


class FakeContext:
    def __init__(self, index):
        self.index = index

    def authorized_evidence(self, project, access, *, limit):
        return tuple(
            item
            for item in self.index.all()
            if item.project == project
            and item.access_scope in access.permissions
        )[:limit]


def _setup():
    project = ProjectRef(tenant_id="t1", project_id="p1")
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="docs", source_id="req-1"),
        content="正式需求内容",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:p1:read",
        content_hash="a" * 64,
    )
    index = InMemoryEvidenceIndex()
    index.add_many((evidence,))
    access = AccessContext(
        tenant_id="t1",
        user_id="u1",
        permissions=frozenset({"project:p1:read"}),
    )
    detail = KnowledgeDetailService(context_engine=FakeContext(index))
    result = KnowledgeResult(
        result_id=str(evidence.id),
        source_layer=SourceLayer.EVIDENCE,
        title="req-1",
        snippet=evidence.content,
        score=1.0,
        evidence_ids=(str(evidence.id),),
        provenance={"content_hash": evidence.content_hash},
        retrieval_channels=("bm25",),
        payload=evidence,
    )
    return project, evidence, access, detail, result, index


def test_snapshot_freeze_and_replay_revalidates_hash() -> None:
    project, evidence, access, detail, result, index = _setup()
    snapshots = KnowledgeSnapshotService(
        detail,
        database=SQLiteDatabase(":memory:"),
    )
    snapshot = snapshots.freeze(
        (result,),
        project=project,
        access=access,
        actor_id="u1",
        chat_id="chat-1",
        retrieval_mode="lexical",
    )

    replayed = snapshots.replay(
        snapshot.snapshot_id,
        project=project,
        access=access,
        actor_id="u1",
        chat_id="chat-1",
    )
    assert replayed[0].content == evidence.content
    assert snapshot.context_hash
    assert snapshot.records[0].retrieval_channels == ("bm25",)

    changed = evidence.model_copy(update={"content": "新内容", "content_hash": "b" * 64})
    index.remove_source_prefix(system="docs", source_id_prefix="req-1")
    index.add_many((changed,))
    with pytest.raises(KnowledgeDetailError, match="hash changed"):
        snapshots.replay(
            snapshot.snapshot_id,
            project=project,
            access=access,
            actor_id="u1",
            chat_id="chat-1",
        )


def test_snapshot_replay_fails_when_access_is_revoked() -> None:
    project, _, access, detail, result, index = _setup()
    snapshots = KnowledgeSnapshotService(detail)
    snapshot = snapshots.freeze(
        (result,),
        project=project,
        access=access,
        actor_id="u1",
        chat_id="chat-1",
        retrieval_mode="lexical",
    )
    denied = AccessContext(tenant_id="t1", user_id="u1", permissions=frozenset())
    with pytest.raises(KnowledgeDetailError, match="authorized"):
        snapshots.replay(
            snapshot.snapshot_id,
            project=project,
            access=denied,
            actor_id="u1",
            chat_id="chat-1",
        )


def test_fact_claim_requires_current_evidence_reference() -> None:
    project, evidence, access, detail, result, _ = _setup()
    current = detail.read(result, project=project, access=access)
    verifier = KnowledgeClaimVerifier()
    assert verifier.verify_fact(
        claim_text="正式需求内容",
        evidence_ids=(str(evidence.id),),
        details=(current,),
    )
    assert not verifier.verify_fact(
        claim_text="没有来源的事实",
        evidence_ids=("missing",),
        details=(current,),
    )
