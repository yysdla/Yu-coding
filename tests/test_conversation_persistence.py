"""SQLite ConversationSession persistence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from project_lens.application.conversation_service import ConversationService
from project_lens.context.conversation_store import (
    InMemoryConversationStore,
    SQLiteConversationStore,
)
from project_lens.domain.conversation import (
    ConversationSession,
    ConversationTurn,
    PinnedIds,
    empty_summary,
)
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _session(**overrides: object) -> ConversationSession:
    project = _project()
    summary = empty_summary(project).model_copy(
        update={
            "active_skill": "incident_diagnosis",
            "unknowns": ("need owner",),
            "next_actions": ("ask who changed it",),
            "pinned_ids": PinnedIds(
                evidence_ids=("ev-1",),
                file_paths=("src/order_service.py",),
            ),
        }
    )
    base = {
        "tenant_id": "demo",
        "chat_id": "chat-persist",
        "user_id": "user-1",
        "project": project,
        "last_run_id": uuid4(),
        "recent_turns": (
            ConversationTurn(
                run_id=uuid4(),
                user_id="user-1",
                text="AttributeError on coupon",
                rewritten_question=None,
            ),
        ),
        "summary": summary,
        "task_scratchpad": {
            "phase": "Validate",
            "files_read": ["src/order_service.py"],
            "test_results": ["pytest: passed"],
            "allow_apply": False,
        },
        "expires_at": datetime.now(timezone.utc) + timedelta(days=3),
    }
    base.update(overrides)
    return ConversationSession.model_validate(base)


def test_sqlite_upsert_and_get_round_trip() -> None:
    store = SQLiteConversationStore(SQLiteDatabase(":memory:"))
    session = _session()
    store.upsert(session)
    loaded = store.get(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.recent_turns[0].text == "AttributeError on coupon"
    assert loaded.summary.active_skill == "incident_diagnosis"
    assert loaded.summary.pinned_ids.file_paths == ("src/order_service.py",)
    assert loaded.task_scratchpad["phase"] == "Validate"
    assert loaded.task_scratchpad["files_read"] == ["src/order_service.py"]
    assert loaded.last_run_id == session.last_run_id


def test_sqlite_get_by_binding_hits_same_session() -> None:
    store = SQLiteConversationStore(SQLiteDatabase(":memory:"))
    session = _session()
    store.upsert(session)
    loaded = store.get_by_binding(
        tenant_id="demo",
        chat_id="chat-persist",
        user_id="user-1",
        project=_project(),
    )
    assert loaded is not None
    assert loaded.session_id == session.session_id


def test_sqlite_expired_session_not_returned() -> None:
    store = SQLiteConversationStore(SQLiteDatabase(":memory:"))
    session = _session(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    store.upsert(session)
    assert store.get(session.session_id) is None
    assert (
        store.get_by_binding(
            tenant_id="demo",
            chat_id="chat-persist",
            user_id="user-1",
            project=_project(),
        )
        is None
    )


def test_conversation_service_record_turn_with_sqlite_store() -> None:
    store = SQLiteConversationStore(SQLiteDatabase(":memory:"))
    service = ConversationService(store=store, recent_turn_limit=4)
    project = _project()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-svc",
        user_id="user-1",
        project=project,
    )
    run_id = uuid4()
    updated = service.record_turn(
        session,
        user_id="user-1",
        text="那是谁改的？",
        rewritten_question="请基于当前故障诊断上下文说明责任人或最近改动人",
        run_id=run_id,
        task_state=None,
    )
    reloaded = store.get(updated.session_id)
    assert reloaded is not None
    assert len(reloaded.recent_turns) == 1
    assert reloaded.recent_turns[0].text == "那是谁改的？"
    assert reloaded.last_run_id == run_id
    same = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-svc",
        user_id="user-1",
        project=project,
    )
    assert same.session_id == updated.session_id


def test_inmemory_store_still_available() -> None:
    store = InMemoryConversationStore()
    session = _session(chat_id="chat-memory")
    store.upsert(session)
    assert store.get(session.session_id) is not None
