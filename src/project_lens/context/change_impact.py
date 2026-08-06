"""Build a read-only project change-impact summary from authorized evidence."""

from __future__ import annotations

from collections.abc import Iterable

from project_lens.domain.models import (
    ChangeImpact,
    Evidence,
    EvidenceType,
    ProjectRef,
    ProjectSnapshot,
    TimelineEvent,
)


def build_change_impact(
    evidence: Iterable[Evidence],
    *,
    project: ProjectRef,
    snapshot: ProjectSnapshot,
    timeline: tuple[TimelineEvent, ...],
) -> ChangeImpact:
    items = tuple(evidence)
    related_events = tuple(timeline[:5])
    impacted_services = _dedupe(
        snapshot.services
        + _services_from_events(related_events)
        + _services_from_evidence(items)
    )
    risks = _dedupe(snapshot.risks + _risk_phrases(items, related_events))
    parts: list[str] = []
    if impacted_services:
        parts.append("Affected services: " + ", ".join(impacted_services[:3]))
    if related_events:
        parts.append(
            "Related events: "
            + "; ".join(
                f"{event.observed_at.date().isoformat()} {event.event_type}: {event.title}"
                for event in related_events[:3]
            )
        )
    if risks:
        parts.append("Risks: " + ", ".join(risks[:3]))
    summary = (
        "Observed project changes. " + " ".join(parts)
        if parts
        else "No authorized change evidence found."
    )
    evidence_ids = tuple(
        dict.fromkeys([item.id for item in items] + [event.evidence_id for event in related_events])
    )
    return ChangeImpact(
        project=project,
        summary=summary,
        affected_services=impacted_services,
        related_events=related_events,
        risks=risks,
        evidence_ids=evidence_ids,
    )


def _services_from_events(events: tuple[TimelineEvent, ...]) -> tuple[str, ...]:
    return tuple(
        ref
        for event in events
        for ref in event.references
        if "service" in ref.lower() or ref.endswith("-service")
    )


def _services_from_evidence(evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    return tuple(item.project.service for item in evidence if item.project.service)


def _risk_phrases(evidence: tuple[Evidence, ...], events: tuple[TimelineEvent, ...]) -> tuple[str, ...]:
    risks: list[str] = []
    for item in evidence:
        lowered = item.content.casefold()
        if item.type == EvidenceType.INCIDENT:
            risks.append("incident evidence requires follow-up")
        if any(marker in lowered for marker in ("regression", "breaking change", "http 500", "outage")):
            risks.append("change may introduce production regression")
    risks.extend(event.title for event in events if event.event_type == "incident")
    return tuple(risks)


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in items if item and item.strip()))
