from datetime import datetime, timedelta, timezone

from project_lens.context.history_retention import HistoryRetentionPolicy, apply_history_retention
from project_lens.context.history_store import InMemoryHistoryMemoryStore, SQLiteHistoryMemoryStore
from project_lens.domain.models import AgentRun, ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


def _run(project: ProjectRef, created_at: datetime) -> AgentRun:
    return AgentRun(
        project=project,
        user_id="u",
        question="历史调查",
        created_at=created_at,
        updated_at=created_at,
    )


def test_retention_is_project_scoped_for_memory_store() -> None:
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    payment = ProjectRef(tenant_id="demo", project_id="payment")
    crm = ProjectRef(tenant_id="demo", project_id="crm")
    store = InMemoryHistoryMemoryStore()
    old_payment = _run(payment, now - timedelta(days=200))
    old_crm = _run(crm, now - timedelta(days=200))
    store.upsert_run(old_payment, ())
    store.upsert_run(old_crm, ())

    report = apply_history_retention(store, payment, HistoryRetentionPolicy(180), now=now)

    assert report.removed_episode_count == 1
    assert store.get_episode(old_payment.id) is None
    assert store.get_episode(old_crm.id) is not None


def test_sqlite_retention_removes_episode_and_observations() -> None:
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    project = ProjectRef(tenant_id="demo", project_id="payment")
    store = SQLiteHistoryMemoryStore(SQLiteDatabase(":memory:"))
    run = _run(project, now - timedelta(days=200))
    store.upsert_run(run, ())

    report = apply_history_retention(store, project, HistoryRetentionPolicy(180), now=now)

    assert report.removed_episode_count == 1
    assert store.get_episode(run.id) is None
    assert store.list_observations(run.id) == ()
