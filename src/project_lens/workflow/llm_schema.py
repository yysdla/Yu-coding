"""Validated LLM expression-enhancement payloads (ContextPrompt outputs only)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LlmClaimDraft(FrozenModel):
    text: str = Field(min_length=1, max_length=2_000)
    claim_type: str = Field(default="inference", max_length=50)
    evidence_ids: tuple[str, ...] = ()


class LlmExpressionOutput(FrozenModel):
    """Read-only expression enhancement. Never authorizes Apply or Memory writes."""

    business_summary: str | None = Field(default=None, max_length=4_000)
    technical_summary: str | None = Field(default=None, max_length=4_000)
    product_summary: str | None = Field(default=None, max_length=4_000)
    hypotheses: tuple[str, ...] = ()
    claim_drafts: tuple[LlmClaimDraft, ...] = ()
    allow_apply: bool = False

    @field_validator("allow_apply")
    @classmethod
    def allow_apply_must_be_false(cls, value: bool) -> bool:
        if value:
            raise ValueError("LLM output must not set allow_apply=True")
        return False


def parse_llm_expression_output(raw: str | None) -> LlmExpressionOutput | None:
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip()
    # Allow fenced JSON from chat models.
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        # Plain prose: treat as business_summary only.
        return LlmExpressionOutput(business_summary=text[:4_000], allow_apply=False)
    if not isinstance(payload, dict):
        return None
    payload = dict(payload)
    payload["allow_apply"] = False
    try:
        return LlmExpressionOutput.model_validate(payload)
    except ValidationError:
        return None
