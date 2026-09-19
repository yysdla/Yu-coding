from datetime import datetime, timezone
from uuid import uuid4

import pytest

from project_lens.application.connector_sync import ConnectorSyncService
from project_lens.application.connector_sync_status import InMemoryConnectorSyncStateStore
from project_lens.application.memory_review_service import MemoryReviewService
from project_lens.context.connectors.base import SyncBatch
from project_lens.context.memory_review_queue import MemoryReviewQueue, SQLiteMemoryReviewQueue
from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.context.source_store import InMemorySourceRecordStore
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.memory import MemoryProposal, MemorySourceRef, MemoryType, ReviewReason
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


def _record(project: ProjectRef, revision: str, *, source_id: str = "requirements/doc") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        source_type=SourceType.FEISHU_DOCUMENT,
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        revision=revision,
        observed_at=datetime.now(timezone.utc),
        access_scope="knowledge/",
        content_hash=("hash-" + revision).ljust(16, "0"),
        content=f"requirements {revision}",
    )


def _memory(store: InMemoryMemoryStore, project: ProjectRef, revision: str):
    proposal = MemoryProposal(
        project=project,
        proposed_by="u1",
        claim_text="Payment callbacks use Kafka",
        memory_type=MemoryType.DECISION,
        subject="payment-service",
        claim_slot="transport",
        evidence_ids=(uuid4(),),
        source_refs=(
            MemorySourceRef(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
                source_id="requirements/doc",
                revision=revision,
            ),
        ),
    )
    store.create_proposal(proposal)
    _, memory = store.decide_proposal(proposal.id, approved=True, decided_by="lead")
    assert memory is not None
    return memory


def test_source_revision_and_delete_open_idempotent_reviews() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    source_store = InMemorySourceRecordStore()
    source_store.put(_record(project, "r1"))
    memories = InMemoryMemoryStore()
    memory = _memory(memories, project, "r1")
    queue = MemoryReviewQueue()
    reviews = MemoryReviewService(memories, queue)
    sync = ConnectorSyncService(
        evidence_index=InMemoryEvidenceIndex(),
        state_store=InMemoryConnectorSyncStateStore(),
        source_store=source_store,
        memory_review_service=reviews,
    )

    class Connector:
        name = "feishu_document"

        async def sync(self, cursor=None):
            return SyncBatch(records=(_record(project, "r2"),))

    result = pytest.importorskip("asyncio").run(sync.sync_connector(Connector(), project=project))
    assert result.updated_count == 0
    opened = queue.list(project=project)
    assert len(opened) == 1
    assert opened[0].memory_id == memory.id
    assert opened[0].reason is ReviewReason.SOURCE_REVISED

    pytest.importorskip("asyncio").run(sync.sync_connector(Connector(), project=project))
    assert len(queue.list(project=project)) == 1


def test_sqlite_review_queue_survives_reopen() -> None:
    database = SQLiteDatabase(":memory:")
    project = ProjectRef(tenant_id="demo", project_id="payment")
    memory_id = uuid4()
    first = SQLiteMemoryReviewQueue(database)
    review = first.open(
        memory_id=memory_id,
        project=project,
        reason=ReviewReason.EVIDENCE_REVOKED,
        trigger_key="evidence:123",
    )
    second = SQLiteMemoryReviewQueue(database)
    restored = second.list(project=project)
    assert restored == (review,)
    resolved = second.resolve(review.review_id, resolved_by="lead", resolution="rechecked")
    assert resolved.status.value == "resolved"
    assert second.list(project=project)[0].resolution == "rechecked"
