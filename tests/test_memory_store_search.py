"""Persistent candidate retrieval for long-term project memory."""

from uuid import uuid4

from project_lens.context.memory_store import SQLiteMemoryStore
from project_lens.domain.memory import MemoryProposal, MemoryType
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


def _approve(store: SQLiteMemoryStore, project: ProjectRef, text: str, memory_type: MemoryType):
    proposal = store.create_proposal(
        MemoryProposal(
            project=project,
            proposed_by="u1",
            claim_text=text,
            memory_type=memory_type,
            evidence_ids=(uuid4(),),
        )
    )
    _updated, memory = store.decide_proposal(proposal.id, approved=True, decided_by="u1")
    assert memory is not None
    return memory


def test_sqlite_memory_search_uses_project_scoped_index_and_excludes_revoked() -> None:
    store = SQLiteMemoryStore(SQLiteDatabase(":memory:"))
    project = ProjectRef(tenant_id="demo", project_id="payment")
    memory = _approve(
        store,
        project,
        "Kafka consumer lag can delay payment callbacks",
        MemoryType.RISK,
    )
    _approve(store, ProjectRef(tenant_id="demo", project_id="crm"), "Kafka is used for CRM events", MemoryType.DECISION)

    candidates = store.search_memories(project, "Kafka consumer lag")
    assert [item.id for item in candidates] == [memory.id]

    replacement = store.create_proposal(
        MemoryProposal(
            project=project,
            proposed_by="u1",
            claim_text="Kafka consumer lag is monitored by the payment SRE",
            memory_type=MemoryType.RISK,
            evidence_ids=(uuid4(),),
            replaces_memory_id=memory.id,
        )
    )
    _updated, new_memory = store.decide_proposal(replacement.id, approved=True, decided_by="u1")
    assert new_memory is not None
    assert all(item.id != memory.id for item in store.search_memories(project, "Kafka"))


def test_sqlite_memory_summary_is_persisted_and_reloaded() -> None:
    database = SQLiteDatabase(":memory:")
    store = SQLiteMemoryStore(database)
    project = ProjectRef(tenant_id="demo", project_id="payment")
    _approve(store, project, "Payment callback owner is Ada", MemoryType.OWNER)
    summary = store.get_memory_summary(project)
    assert summary
    assert summary[0].active_count == 1

    reloaded = SQLiteMemoryStore(database)
    restored = reloaded.get_memory_summary(project)
    assert restored == summary
