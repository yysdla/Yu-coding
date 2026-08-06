"""ConversationSession survives process restart via SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from project_lens.application.conversation_service import ConversationService
from project_lens.context.conversation_store import SQLiteConversationStore
from project_lens.domain.models import (
    Claim,
    ClaimType,
    Evidence,
    EvidenceType,
    ProjectAnswer,
    ProjectRef,
    SourceRef,
)
from project_lens.persistence.sqlite import SQLiteDatabase
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.workflow.context_pack import TaskScratchpad
from project_lens.workflow.followup import FollowupRewriter

_TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _answer(project: ProjectRef) -> ProjectAnswer:
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local", source_id="src/order_service.py"),
        content="coupon_id = request.coupon.id",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    claim = Claim(
        text="optional coupon can be None",
        type=ClaimType.FACT,
        evidence_ids=(evidence.id,),
    )
    return ProjectAnswer(
        project=project,
        skill="incident_diagnosis",
        confidence=0.7,
        status="identified",
        business_summary="下单可能因 coupon 为空失败",
        technical_summary="AttributeError on request.coupon.id",
        claims=(claim,),
        evidence=(evidence,),
        unknowns=("缺少完整日志窗口",),
        recommended_actions=(),
    )


def test_sqlite_session_recovers_after_new_database_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "conversation_restart.db"
    project = _project()

    # Process A: create session + turn + scratchpad.
    store_a = SQLiteConversationStore(SQLiteDatabase(str(db_path)))
    service_a = ConversationService(store=store_a)
    session_a = service_a.get_or_create(
        tenant_id="demo",
        chat_id="chat-restart",
        user_id="user-1",
        project=project,
    )
    run_id = uuid4()
    session_a = service_a.record_turn(
        session_a,
        user_id="user-1",
        text="AttributeError on coupon",
        rewritten_question=None,
        run_id=run_id,
        answer=_answer(project),
        task_state=TaskScratchpad(
            phase="Validate",
            files_read=("src/order_service.py",),
            allow_apply=False,
        ),
    )
    session_id = session_a.session_id

    # Process B: new SQLite connection to the same file (simulates restart).
    store_b = SQLiteConversationStore(SQLiteDatabase(str(db_path)))
    service_b = ConversationService(store=store_b)
    recovered = service_b.get_or_create(
        tenant_id="demo",
        chat_id="chat-restart",
        user_id="user-1",
        project=project,
    )
    assert recovered.session_id == session_id
    assert recovered.last_run_id == run_id
    assert recovered.summary.active_skill == "incident_diagnosis"
    assert recovered.recent_turns[0].text == "AttributeError on coupon"
    assert recovered.task_scratchpad.get("files_read") == ["src/order_service.py"]

    question, rewrite = service_b.prepare_question(recovered, "那是谁改的？")
    assert rewrite is not None
    assert "故障诊断" in rewrite
    assert question == rewrite
    assert FollowupRewriter().rewrite("影响哪里？", recovered) is not None


def test_compress_then_restart_keeps_how_to_fix_traceback(tmp_path: Path) -> None:
    """L1 eviction + new DB connection must still rewrite 「怎么修」 with traceback."""

    db_path = tmp_path / "conversation_compress_restart.db"
    project = _project()
    bus = LifecycleBus()
    store_a = SQLiteConversationStore(SQLiteDatabase(str(db_path)))
    service_a = ConversationService(
        store=store_a,
        recent_turn_limit=2,
        lifecycle=bus,
    )
    session = service_a.get_or_create(
        tenant_id="demo",
        chat_id="chat-compress",
        user_id="user-1",
        project=project,
    )
    assert bus.of_type(LifecycleEventType.SESSION_CREATED)
    assert bus.of_type(LifecycleEventType.SESSION_SAVED)

    session = service_a.record_turn(
        session,
        user_id="user-1",
        text=_TRACEBACK,
        rewritten_question=None,
        run_id=uuid4(),
        answer=_answer(project),
    )
    assert session.summary.active_topic.get("prior_traceback")
    # Fill past L1 limit so the traceback turn is compressed out of recent_turns.
    for index in range(3):
        session = service_a.record_turn(
            session,
            user_id="user-1",
            text=f"filler-{index}",
            rewritten_question=None,
            run_id=uuid4(),
        )
    assert all(_TRACEBACK not in turn.text for turn in session.recent_turns)
    assert session.summary.compression_cycle >= 1
    assert "AttributeError" in session.summary.active_topic["prior_traceback"]
    session_id = session.session_id

    store_b = SQLiteConversationStore(SQLiteDatabase(str(db_path)))
    bus_b = LifecycleBus()
    service_b = ConversationService(store=store_b, recent_turn_limit=2, lifecycle=bus_b)
    recovered = service_b.get_or_create(
        tenant_id="demo",
        chat_id="chat-compress",
        user_id="user-1",
        project=project,
    )
    assert recovered.session_id == session_id
    loaded = bus_b.of_type(LifecycleEventType.SESSION_LOADED)
    assert loaded
    assert loaded[-1].payload["recovered"] is True
    assert loaded[-1].payload["has_prior_traceback"] is True

    rewritten = FollowupRewriter().rewrite("怎么修？", recovered)
    assert rewritten is not None
    assert "Engineering" in rewritten or "修复提案" in rewritten
    assert "AttributeError" in rewritten
    assert "order_service.py" in rewritten


def test_corrupt_sqlite_payload_is_dropped(tmp_path: Path) -> None:
    db_path = tmp_path / "conversation_corrupt.db"
    project = _project()
    database = SQLiteDatabase(str(db_path))
    store = SQLiteConversationStore(database)
    service = ConversationService(store=store)
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-corrupt",
        user_id="user-1",
        project=project,
    )
    database.execute(
        "UPDATE conversation_sessions SET payload = ? WHERE session_id = ?",
        ("{not-json", str(session.session_id)),
    )
    recovered = store.get_by_binding(
        tenant_id="demo",
        chat_id="chat-corrupt",
        user_id="user-1",
        project=project,
    )
    assert recovered is None
    # Clean recreate after corrupt drop.
    fresh = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-corrupt",
        user_id="user-1",
        project=project,
    )
    assert fresh.session_id != session.session_id
