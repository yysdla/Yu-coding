"""Build normalized project timeline events from authorized evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, TimelineEvent


def build_timeline_events(
    evidence: Iterable[Evidence],
    *,
    project: ProjectRef,
) -> tuple[TimelineEvent, ...]:
    events = [
        _event_from_evidence(item, project=project)
        for item in evidence
        if _timeline_event_type(item) is not None
    ]
    return tuple(
        sorted(
            events,
            key=lambda item: (
                _event_priority(item.event_type),
                item.observed_at,
                item.source.source_id,
            ),
            reverse=True,
        )
    )


def _event_from_evidence(item: Evidence, *, project: ProjectRef) -> TimelineEvent:
    event_type = _timeline_event_type(item)
    if event_type is None:
        raise ValueError("evidence type cannot be converted to a timeline event")
    return TimelineEvent(
        project=project,
        event_type=event_type,
        title=_event_title(item, event_type),
        observed_at=item.observed_at,
        source=item.source,
        evidence_id=item.id,
        summary=_event_summary(item),
        references=_event_references(item),
    )


def _timeline_event_type(item: Evidence) -> str | None:
    if item.type == EvidenceType.INCIDENT:
        return "incident"
    if item.type == EvidenceType.COMMIT:
        return "commit"
    if item.type == EvidenceType.PULL_REQUEST:
        return "pull_request"
    if item.type == EvidenceType.TASK:
        if str(item.metadata.get("kind") or "") == "release":
            return "release"
        return "task"
    if item.type == EvidenceType.DOCUMENT:
        return "document"
    if item.type == EvidenceType.CODE:
        return "code"
    return None


def _event_title(item: Evidence, event_type: str) -> str:
    title = item.metadata.get("title")
    if title:
        return str(title)[:300]
    if event_type == "code":
        symbol = item.metadata.get("symbol")
        if symbol:
            return f"Code symbol indexed: {symbol}"
    first_line = next((line.strip() for line in item.content.splitlines() if line.strip()), "")
    if ":" in first_line and event_type in {"incident", "task", "release"}:
        return first_line.split(":", 1)[1].strip()[:300]
    return first_line[:300] or item.source.source_id


def _event_summary(item: Evidence) -> str:
    content = re.sub(r"\s+", " ", item.content).strip()
    return content[:1_000]


def _event_references(item: Evidence) -> tuple[str, ...]:
    refs = [item.source.source_id]
    for key in (
        "id",
        "service",
        "symbol",
        "file",
        "commit_sha",
        "branch",
        "author",
        "task_id",
        "release_id",
        "version",
        "related_incident_id",
        "related_commit_sha",
    ):
        value = item.metadata.get(key)
        if value:
            refs.append(str(value))
    files_changed = item.metadata.get("files_changed")
    if isinstance(files_changed, list):
        refs.extend(str(path) for path in files_changed[:5])
    commit_shas = item.metadata.get("commit_shas")
    if isinstance(commit_shas, list):
        refs.extend(str(sha) for sha in commit_shas[:5])
    if item.project.service:
        refs.append(item.project.service)
    return tuple(dict.fromkeys(refs))


def _event_priority(event_type: str) -> int:
    priorities = {
        "incident": 6,
        "release": 5,
        "pull_request": 5,
        "commit": 4,
        "task": 3,
        "document": 2,
        "code": 1,
    }
    return priorities.get(event_type, 0)
