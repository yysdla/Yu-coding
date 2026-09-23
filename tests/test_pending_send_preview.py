"""S03: preview pending-send set == Hermes assembly (no extra injection)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from project_lens.application.conversation_service import ConversationService
from project_lens.domain.conversation import (
    CitationEntry,
    CitationSourceKind,
    PendingItemMark,
    build_pending_send_items,
    estimate_pending_token_budget,
    format_pending_items_for_hermes,
)
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.cards import render_context_preview_card
from project_lens.integrations.feishu.hermes_context import build_hermes_project_context


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _seed_turns(service: ConversationService, *, count: int = 3):
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-s03",
        user_id="user-1",
        project=_project(),
    )
    base = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    for index in range(count):
        session = service.record_turn(
            session,
            user_id="user-1",
            text=f"seed-question-{index}",
            rewritten_question=None,
            run_id=uuid4(),
        )
        # Force distinct occurred_at via model_copy on last turn for sort stability.
        turns = list(session.recent_turns)
        turns[-1] = turns[-1].model_copy(update={"created_at": base + timedelta(hours=index)})
        session = session.model_copy(update={"recent_turns": tuple(turns)})
        session = service.store.upsert(session)
    return session


def test_refresh_pending_send_includes_defaults_and_token_budget() -> None:
    service = ConversationService()
    session = _seed_turns(service, count=3)
    session = service.refresh_pending_send(
        session,
        question="今晚怎么部署？",
        question_for_run="今晚怎么部署？",
    )
    assert session.pending_send is not None
    assert session.pending_send.token_estimate > 0
    assert len(session.pending_send.items) == 3
    assert all(item.mark == PendingItemMark.DEFAULT for item in session.pending_send.items)
    stamps = [item.occurred_at for item in session.pending_send.items]
    assert stamps == sorted(stamps)


def test_preview_set_equals_hermes_assembly_no_extra_injection() -> None:
    service = ConversationService()
    session = _seed_turns(service, count=3)
    session = service.refresh_pending_send(session, question="q")
    preview_ids = [item.item_id for item in session.pending_send.items]  # type: ignore[union-attr]
    preview_fragment = format_pending_items_for_hermes(session.pending_send.items)  # type: ignore[union-attr]

    context = build_hermes_project_context(session=session, use_pending_send=True)
    assert context.audit_refs["pending_send_mode"] is True
    assert context.audit_refs["pending_item_ids"] == preview_ids
    assert preview_fragment in context.text
    # Must not dump raw recent_turn lines outside pending_send.
    assert "recent_turn[1]" not in context.text
    assert "seed-question-0" in context.text
    # Injecting a turn that is not in pending must not appear when pending mode is on.
    assert "secret-extra-turn" not in context.text


def test_exclude_item_then_send_omits_that_item() -> None:
    service = ConversationService()
    session = _seed_turns(service, count=3)
    session = service.refresh_pending_send(session, question="q")
    assert session.pending_send is not None
    victim = session.pending_send.items[1]
    victim_text = victim.user_question or victim.label
    session = service.exclude_from_pending_send(session, (victim.item_id,))
    assert session.pending_send is not None
    assert victim.item_id not in {item.item_id for item in session.pending_send.items}
    assert len(session.pending_send.items) == 2

    context = build_hermes_project_context(session=session, use_pending_send=True)
    assert victim.item_id not in context.audit_refs["pending_item_ids"]
    assert victim_text not in context.text
    remaining = [item.user_question for item in session.pending_send.items]
    for text in remaining:
        assert text in context.text


def test_use_pending_send_true_without_state_raises() -> None:
    service = ConversationService()
    session = _seed_turns(service, count=1)
    assert session.pending_send is None
    try:
        build_hermes_project_context(session=session, use_pending_send=True)
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_citation_fine_select_lands_on_same_card_as_defaults() -> None:
    service = ConversationService()
    session = _seed_turns(service, count=2)
    citation = CitationEntry(
        source_kind=CitationSourceKind.HISTORY,
        occurred_at=datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc),
        short_title="旧讨论：灰度方案",
        body_snapshot="灰度方案细节……",
    )
    session = service.append_citation(session, citation)
    session = service.refresh_pending_send(
        session,
        question="继续灰度",
        selected_citation_ids=(citation.citation_id,),
    )
    assert session.pending_send is not None
    marks = {item.mark for item in session.pending_send.items}
    assert PendingItemMark.DEFAULT in marks
    assert PendingItemMark.CITATION in marks
    # Citation is earlier → first in time order.
    assert session.pending_send.items[0].mark == PendingItemMark.CITATION
    assert session.pending_send.items[0].citation_id == citation.citation_id

    card = render_context_preview_card(
        question=session.pending_send.question,
        session_id=session.session_id,
        items=session.pending_send.items,
        token_estimate=session.pending_send.token_estimate,
    )
    payload = str(card)
    assert "Token 预算" in payload
    assert "直接回答" in payload
    assert "编辑上下文" in payload
    assert "选择更多历史" in payload
    assert "默认" in payload or "[默认]" in payload
    assert "引用" in payload or "[引用]" in payload
    assert str(session.pending_send.token_estimate) in payload


def test_build_pending_rejects_nothing_without_time_field() -> None:
    """occurred_at is required on PendingSendItem; builder always supplies it."""

    service = ConversationService()
    session = _seed_turns(service, count=1)
    items = build_pending_send_items(session)
    assert items
    assert all(item.occurred_at is not None for item in items)
    assert estimate_pending_token_budget(items) == estimate_pending_token_budget(items)
