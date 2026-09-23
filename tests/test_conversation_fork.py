"""S06: conversation fork persistence (sibling lines + binding → active)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from project_lens.application.conversation_service import ConversationService
from project_lens.context.conversation_store import (
    InMemoryConversationStore,
    SQLiteConversationStore,
)
from project_lens.domain.conversation import (
    CitationEntry,
    CitationSourceKind,
    ConversationSession,
    ConversationTurn,
    DateWindow,
    empty_summary,
)
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.cards import render_context_preview_card
from project_lens.persistence.sqlite import SQLiteDatabase


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _seed(service: ConversationService, *, chat_id: str = "chat-fork") -> ConversationSession:
    session = service.get_or_create(
        tenant_id="demo",
        chat_id=chat_id,
        user_id="user-1",
        project=_project(),
    )
    session = service.record_turn(
        session,
        user_id="user-1",
        text="shared-before-fork",
        rewritten_question=None,
        run_id=uuid4(),
    )
    return service.refresh_pending_send(session, question="继续？")


def test_fork_copies_state_and_stops_parent() -> None:
    service = ConversationService()
    parent = _seed(service)
    assert parent.branch_name == "A"
    assert parent.pending_send is not None
    parent_turn_count = len(parent.recent_turns)
    parent_citation_count = len(parent.citations)

    child = service.fork_session(parent)
    assert child.session_id != parent.session_id
    assert child.parent_session_id == parent.session_id
    assert child.branch_name == "B"
    assert child.write_stopped is False
    assert len(child.recent_turns) == parent_turn_count
    assert len(child.citations) == parent_citation_count
    assert child.pending_send is not None
    assert child.pending_send.question == parent.pending_send.question

    reloaded_parent = service.store.get(parent.session_id)
    assert reloaded_parent is not None
    assert reloaded_parent.write_stopped is True

    active = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-fork",
        user_id="user-1",
        project=_project(),
    )
    assert active.session_id == child.session_id


def test_parent_rejects_writes_after_fork_child_accepts() -> None:
    service = ConversationService()
    parent = _seed(service)
    child = service.fork_session(parent)
    frozen = service.store.get(parent.session_id)
    assert frozen is not None

    with pytest.raises(ValueError, match="write_stopped"):
        service.record_turn(
            frozen,
            user_id="user-1",
            text="should-fail-on-A",
            rewritten_question=None,
            run_id=uuid4(),
        )

    child = service.record_turn(
        child,
        user_id="user-1",
        text="only-on-B",
        rewritten_question=None,
        run_id=uuid4(),
    )
    assert any(turn.text == "only-on-B" for turn in child.recent_turns)
    assert all(turn.text != "only-on-B" for turn in (service.store.get(parent.session_id) or parent).recent_turns)


def test_switch_back_to_a_isolates_turns() -> None:
    service = ConversationService()
    parent = _seed(service, chat_id="chat-switch")
    child = service.fork_session(parent)
    child = service.record_turn(
        child,
        user_id="user-1",
        text="b-only",
        rewritten_question=None,
        run_id=uuid4(),
    )

    resumed = service.switch_session(child, parent.session_id)
    assert resumed.session_id == parent.session_id
    assert resumed.write_stopped is False
    resumed = service.record_turn(
        resumed,
        user_id="user-1",
        text="a-only",
        rewritten_question=None,
        run_id=uuid4(),
    )

    line_a = service.store.get(parent.session_id)
    line_b = service.store.get(child.session_id)
    assert line_a is not None and line_b is not None
    assert any(turn.text == "a-only" for turn in line_a.recent_turns)
    assert all(turn.text != "b-only" for turn in line_a.recent_turns)
    assert any(turn.text == "b-only" for turn in line_b.recent_turns)
    assert all(turn.text != "a-only" for turn in line_b.recent_turns)

    with pytest.raises(ValueError, match="not the active branch"):
        service.record_turn(
            line_b,
            user_id="user-1",
            text="stale-b-card",
            rewritten_question=None,
            run_id=uuid4(),
        )


def test_fork_survives_sqlite_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "fork.db"
    store = SQLiteConversationStore(SQLiteDatabase(str(db_path)))
    service = ConversationService(store=store)
    parent = _seed(service, chat_id="chat-persist-fork")
    # Attach date window + citation so copy is observable after restart.
    window = DateWindow(
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )
    parent = parent.model_copy(update={"date_window": window})
    parent = service.store.upsert(parent)
    parent = service.append_citation(
        parent,
        CitationEntry(
            source_kind=CitationSourceKind.HISTORY,
            occurred_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
            short_title="pinned-history",
            body_snapshot="body for citation",
        ),
    )
    child = service.fork_session(parent)
    child_id = child.session_id
    parent_id = parent.session_id

    # Simulate process restart with a fresh store on the same file.
    store2 = SQLiteConversationStore(SQLiteDatabase(str(db_path)))
    service2 = ConversationService(store=store2)
    active = service2.get_or_create(
        tenant_id="demo",
        chat_id="chat-persist-fork",
        user_id="user-1",
        project=_project(),
    )
    assert active.session_id == child_id
    branches = service2.list_branches(active)
    names = {item.branch_name for item in branches}
    assert names == {"A", "B"}
    assert {item.session_id for item in branches} == {parent_id, child_id}

    line_b = service2.store.get(child_id)
    assert line_b is not None
    assert line_b.date_window is not None
    assert line_b.date_window.start == window.start
    assert any(item.short_title == "pinned-history" for item in line_b.citations)

    resumed = service2.switch_session(active, parent_id)
    assert resumed.session_id == parent_id
    resumed = service2.record_turn(
        resumed,
        user_id="user-1",
        text="after-restart-on-A",
        rewritten_question=None,
        run_id=uuid4(),
    )
    assert any(turn.text == "after-restart-on-A" for turn in resumed.recent_turns)
    line_b_again = service2.store.get(child_id)
    assert line_b_again is not None
    assert all(turn.text != "after-restart-on-A" for turn in line_b_again.recent_turns)


def test_upsert_keeps_sibling_sessions() -> None:
    store = SQLiteConversationStore(SQLiteDatabase(":memory:"))
    project = _project()
    a = ConversationSession(
        tenant_id="demo",
        chat_id="chat-siblings",
        user_id="user-1",
        project=project,
        summary=empty_summary(project),
        branch_name="A",
        recent_turns=(
            ConversationTurn(user_id="user-1", text="on-a", rewritten_question=None),
        ),
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    store.upsert(a)
    store.set_active_session(a)
    b = ConversationSession(
        tenant_id="demo",
        chat_id="chat-siblings",
        user_id="user-1",
        project=project,
        summary=empty_summary(project),
        branch_name="B",
        parent_session_id=a.session_id,
        recent_turns=(
            ConversationTurn(user_id="user-1", text="on-b", rewritten_question=None),
        ),
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    store.upsert(b)
    store.set_active_session(b)
    # Updating B must not delete A.
    b2 = b.model_copy(
        update={
            "recent_turns": b.recent_turns
            + (ConversationTurn(user_id="user-1", text="on-b-2", rewritten_question=None),)
        }
    )
    store.upsert(b2)
    siblings = store.list_by_binding(
        tenant_id="demo",
        chat_id="chat-siblings",
        user_id="user-1",
        project=project,
    )
    assert {item.branch_name for item in siblings} == {"A", "B"}
    assert store.get(a.session_id) is not None


def test_preview_card_exposes_fork_and_switch_actions() -> None:
    from project_lens.domain.conversation import SessionBranchInfo

    branches = (
        SessionBranchInfo(
            session_id=uuid4(),
            branch_name="A",
            write_stopped=True,
            is_active=False,
        ),
        SessionBranchInfo(
            session_id=uuid4(),
            branch_name="B",
            parent_session_id=None,
            write_stopped=False,
            is_active=True,
        ),
    )
    card = render_context_preview_card(
        question="q",
        session_id=branches[1].session_id,
        items=(),
        token_estimate=1,
        branches=branches,
        active_branch_name="B",
    )
    payload = str(card)
    assert "context_fork_branch" in payload
    assert "context_switch_branch" in payload
    assert "切换·A" in payload
    assert "新建分支" in payload


def test_inmemory_fork_list_and_active() -> None:
    store = InMemoryConversationStore()
    service = ConversationService(store=store)
    parent = _seed(service, chat_id="chat-mem")
    child = service.fork_session(parent)
    branches = service.list_branches(child)
    assert len(branches) == 2
    assert sum(1 for item in branches if item.is_active) == 1
    active = store.get_by_binding(
        tenant_id="demo",
        chat_id="chat-mem",
        user_id="user-1",
        project=_project(),
    )
    assert active is not None
    assert active.session_id == child.session_id
