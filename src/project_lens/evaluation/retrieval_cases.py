"""Validated, versioned retrieval evaluation cases."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Difficulty = Literal["simple", "medium", "hard", "very_hard"]


class RetrievalCase(BaseModel):
    """A single deterministic retrieval expectation.

    Object and source identifiers are intentionally separate: source identifiers
    remain stable across re-indexing while object identifiers can be UUIDs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    chat_id: str | None = Field(default=None, max_length=200)
    query: str = Field(min_length=1, max_length=20_000)
    expected_intent: str | None = Field(default=None, max_length=100)
    as_of: str | None = None
    relevant_object_ids: tuple[str, ...] = ()
    relevant_source_keys: tuple[str, ...] = ()
    forbidden_object_ids: tuple[str, ...] = ()
    expected_evidence_ids: tuple[str, ...] = ()
    expected_abstention: bool = False
    expected_conflict: bool = False
    difficulty: Difficulty = "medium"
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_identifiers(self) -> "RetrievalCase":
        relevant = set(self.relevant_object_ids)
        forbidden = set(self.forbidden_object_ids)
        overlap = relevant & forbidden
        if overlap:
            raise ValueError(
                f"relevant_object_ids and forbidden_object_ids overlap: {sorted(overlap)}"
            )
        if not relevant and not self.relevant_source_keys and not self.expected_abstention:
            raise ValueError(
                "non-abstention cases require relevant_object_ids or relevant_source_keys"
            )
        return self


def load_retrieval_cases(path: Path) -> tuple[RetrievalCase, ...]:
    """Load a JSON array or JSONL file and validate uniqueness and shape."""

    raw = path.read_text(encoding="utf-8")
    payload: Any
    if path.suffix.casefold() in {".jsonl", ".ndjson"}:
        payload = [
            json.loads(line)
            for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    else:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload = payload.get("cases")
    if not isinstance(payload, list):
        raise ValueError("retrieval dataset must be a JSON array or JSONL file")

    cases = tuple(_normalize_case(item) for item in payload)
    ids = [item.id for item in cases]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"retrieval dataset contains duplicate ids: {duplicates}")
    return cases


def dataset_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_case(value: Any) -> RetrievalCase:
    if not isinstance(value, dict):
        raise ValueError("each retrieval case must be an object")
    payload = dict(value)
    if "relevant_sources" in payload and "relevant_source_keys" not in payload:
        payload["relevant_source_keys"] = payload.pop("relevant_sources")
    for field in (
        "relevant_object_ids",
        "relevant_source_keys",
        "forbidden_object_ids",
        "expected_evidence_ids",
        "tags",
    ):
        if field in payload and isinstance(payload[field], list):
            payload[field] = tuple(str(item) for item in payload[field])
    return RetrievalCase.model_validate(payload)


__all__ = ["Difficulty", "RetrievalCase", "dataset_fingerprint", "load_retrieval_cases"]
