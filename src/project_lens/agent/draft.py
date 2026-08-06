"""Structured answer draft produced by ProjectInvestigationAgent."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DraftFact(BaseModel):
    text: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)


class AnswerDraft(BaseModel):
    """LLM / stub planner final structured output before harness verification."""

    facts: list[DraftFact] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    business_summary: str = ""
    technical_summary: str = ""
    tools_used: list[str] = Field(default_factory=list)
    stop_reason: str = "stop"

    @classmethod
    def from_json_text(cls, text: str) -> "AnswerDraft":
        import json
        import re

        cleaned = text.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
        if fenced:
            cleaned = fenced.group(1).strip()
        # Tolerate leading prose before the JSON object.
        if not cleaned.startswith("{"):
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                cleaned = cleaned[start : end + 1]
        payload: dict[str, Any] = json.loads(cleaned)
        facts_raw = payload.get("facts") or []
        facts: list[DraftFact] = []
        for item in facts_raw:
            if isinstance(item, dict):
                facts.append(
                    DraftFact(
                        text=str(item.get("text") or "").strip() or "(empty)",
                        citations=[str(c) for c in (item.get("citations") or [])],
                    )
                )
            elif isinstance(item, str) and item.strip():
                facts.append(DraftFact(text=item.strip(), citations=[]))
        return cls(
            facts=facts,
            inferences=[str(x) for x in (payload.get("inferences") or []) if str(x).strip()],
            unknowns=[str(x) for x in (payload.get("unknowns") or []) if str(x).strip()],
            next_actions=[
                str(x) for x in (payload.get("next_actions") or []) if str(x).strip()
            ],
            business_summary=str(payload.get("business_summary") or ""),
            technical_summary=str(payload.get("technical_summary") or ""),
            tools_used=[str(x) for x in (payload.get("tools_used") or [])],
            stop_reason=str(payload.get("stop_reason") or "stop"),
        )


def parse_citation_id(raw: str) -> UUID | None:
    try:
        return UUID(str(raw))
    except ValueError:
        return None
