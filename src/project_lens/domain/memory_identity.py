"""Stable, server-owned identity helpers for project memory facts."""

from __future__ import annotations

import re

from project_lens.domain.models import ProjectRef


def normalize_subject(value: str | None) -> str:
    """Normalize a subject for deterministic fact identity and matching."""

    if not value:
        return ""
    normalized = value.casefold().strip()
    normalized = re.sub(r"[\\/]+", "-", normalized)
    normalized = re.sub(r"[-_\s]+", "-", normalized)
    return normalized.strip("- /")


def normalize_claim_text(value: str) -> str:
    return "".join(value.casefold().split())


def build_fact_key(
    *,
    project: ProjectRef,
    memory_type: object,
    subject: str | None,
    claim_text: str,
    claim_slot: str | None = None,
) -> str | None:
    """Build a project-scoped key; return None when no stable subject/slot exists."""

    normalized_subject = normalize_subject(subject)
    normalized_slot = normalize_subject(claim_slot)
    if not normalized_subject and not normalized_slot:
        return None
    kind = getattr(memory_type, "value", memory_type)
    identity = normalized_slot or normalize_claim_text(claim_text)[:120]
    return ":".join(
        (
            normalize_subject(project.tenant_id),
            normalize_subject(project.project_id),
            normalize_subject(str(kind)),
            normalized_subject or "_",
            identity,
        )
    )
