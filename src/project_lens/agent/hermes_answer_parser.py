"""Parse Hermes final text into the shared ProjectLens answer draft contract."""

from __future__ import annotations

from project_lens.agent.draft import AnswerDraft


def parse_structured_hermes_answer(text: str) -> AnswerDraft | None:
    """Return a draft only when Hermes emitted the JSON AnswerDraft shape."""

    raw = text.strip()
    if not raw:
        return None
    try:
        return AnswerDraft.from_json_text(raw)
    except (ValueError, TypeError):
        return None


def parse_hermes_answer(text: str) -> AnswerDraft:
    """Parse structured Hermes output without asserting unverified facts.

    Hermes may return Markdown or JSON.  Only JSON fields that conform to the
    existing AnswerDraft contract are accepted; plain text becomes an unknown
    until an Evidence Ledger is attached by a later verification stage.
    """

    raw = text.strip()
    if not raw:
        return AnswerDraft(unknowns=["Hermes 未返回最终答案。"])
    structured = parse_structured_hermes_answer(raw)
    if structured is not None:
        return structured
    return AnswerDraft(
        conclusion=raw[:2_000],
        business_summary=raw[:2_000],
        unknowns=["Hermes 返回了未结构化文本，尚未完成 Evidence 引用校验。"],
    )
