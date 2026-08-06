from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from project_lens.domain.models import AgentRun, ProjectRef
from project_lens.persistence.sqlite import (
    SQLiteDatabase,
    SQLiteEventDeduplicator,
    SQLiteEventSink,
    SQLiteRunRepository,
)
from project_lens.runtime.events import AgentEvent, AgentEventType
from project_lens.main import create_app


def test_sqlite_run_and_events_survive_repository_recreation(tmp_path: Path) -> None:
    database_path = str(tmp_path / "project-lens.db")
    project = ProjectRef(tenant_id="demo", project_id="payment")
    run = AgentRun(project=project, user_id="u1", question="status?")

    first_db = SQLiteDatabase(database_path)
    SQLiteRunRepository(first_db).add(run)
    sink = SQLiteEventSink(first_db)

    import asyncio

    asyncio.run(
        sink.emit(
            AgentEvent(
                run_id=run.id,
                trace_id=run.trace_id,
                type=AgentEventType.RUN_STARTED,
            )
        )
    )

    second_db = SQLiteDatabase(database_path)
    restored = SQLiteRunRepository(second_db).get(run.id)
    restored_events = SQLiteEventSink(second_db).for_run(run.id)

    assert restored is not None
    assert restored.id == run.id
    assert restored_events[0].type == AgentEventType.RUN_STARTED


def test_feishu_event_deduplication_survives_database_recreation(tmp_path: Path) -> None:
    database_path = str(tmp_path / "project-lens.db")

    first = SQLiteEventDeduplicator(SQLiteDatabase(database_path))
    second = SQLiteEventDeduplicator(SQLiteDatabase(database_path))

    assert first.mark_seen("evt-1") is True
    assert second.mark_seen("evt-1") is False
    assert second.mark_seen("evt-2") is True


def test_approval_can_be_created_and_decided_once() -> None:
    client = TestClient(create_app())
    run_response = client.post(
        "/api/v1/runs",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "question": "status?",
        },
    )
    run_id = run_response.json()["run_id"]
    create_response = client.post(
        f"/api/v1/runs/{run_id}/approvals",
        json={"action_id": str(uuid4()), "requested_by": "u1"},
    )

    assert create_response.status_code == 201
    approval = create_response.json()
    assert approval["status"] == "pending"

    decision = client.post(
        f"/api/v1/approvals/{approval['id']}/decision",
        json={"approved": True, "decided_by": "manager-1"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"

    second_decision = client.post(
        f"/api/v1/approvals/{approval['id']}/decision",
        json={"approved": False, "decided_by": "manager-2"},
    )
    assert second_decision.status_code == 409
