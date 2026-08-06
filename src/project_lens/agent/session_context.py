"""Build investigation briefing from ConversationSession (read-only)."""

from __future__ import annotations

from project_lens.domain.conversation import ConversationSession


def build_investigation_session_brief(
    session: ConversationSession | None,
    *,
    max_turns: int = 3,
) -> str:
    """Compact prior-turn context for the investigation system prompt."""

    if session is None:
        return ""
    lines: list[str] = ["Prior conversation context (read-only):"]
    summary = session.summary
    if summary.last_assistant_summary:
        lines.append(f"- last_answer: {summary.last_assistant_summary[:240]}")
    if summary.session_intent:
        lines.append(f"- session_intent: {summary.session_intent[:200]}")
    if summary.active_skill:
        lines.append(f"- active_skill: {summary.active_skill}")
    topic = summary.active_topic or {}
    if topic.get("business_summary"):
        lines.append(f"- prior_topic: {str(topic['business_summary'])[:200]}")
    paths = tuple(summary.pinned_ids.file_paths[:8])
    if paths:
        lines.append(f"- pinned_files: {', '.join(paths)}")
    symbols = tuple(summary.pinned_ids.symbol_names[:8])
    if symbols:
        lines.append(f"- pinned_symbols: {', '.join(symbols)}")
    if summary.unknowns:
        lines.append(f"- prior_unknowns: {'; '.join(summary.unknowns[:3])}")
    turns = session.recent_turns[-max(1, max_turns) :]
    if turns:
        lines.append("- recent_turns:")
        for turn in turns:
            text = (turn.rewritten_question or turn.text).strip().replace("\n", " ")
            lines.append(f"  - user: {text[:160]}")
    if len(lines) == 1:
        return ""
    lines.append(
        "When the user asks a short follow-up, reuse pinned files/symbols "
        "and prior unknowns before searching from scratch."
    )
    return "\n".join(lines)


def prior_file_paths(session: ConversationSession | None) -> tuple[str, ...]:
    if session is None:
        return ()
    return tuple(session.summary.pinned_ids.file_paths[:8])


def prior_search_hints(session: ConversationSession | None) -> tuple[str, ...]:
    """Keywords for stub/live planners on follow-up turns."""

    if session is None:
        return ()
    hints: list[str] = []
    hints.extend(session.summary.pinned_ids.file_paths[:4])
    hints.extend(session.summary.pinned_ids.symbol_names[:4])
    topic = session.summary.active_topic or {}
    for key in ("skill", "status"):
        value = topic.get(key)
        if value:
            hints.append(str(value))
    for turn in session.recent_turns[-2:]:
        text = turn.rewritten_question or turn.text
        for token in ("order_service", "create_order", "coupon", "订单", "架构"):
            if token.casefold() in text.casefold():
                hints.append(token)
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for item in hints:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return tuple(out[:12])
