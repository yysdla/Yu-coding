"""Read-only release/deploy timeline correlation for ProjectOps answers."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from project_lens.domain.models import Evidence, ProjectRef, TimelineEvent
from project_lens.domain.ops import OpsFinding
from project_lens.workflow.models import FrozenModel

NEAR_RELEASE_WINDOW_MINUTES = 6 * 60


class TimelineCorrelation(FrozenModel):
    ordered_events: tuple[str, ...] = ()
    nearest_release_id: str | None = None
    nearest_release_delta_minutes: int | None = None
    ops_after_release: bool | None = None
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()


def build_timeline_correlation(
    *,
    timeline: tuple[TimelineEvent, ...],
    ops_finding: OpsFinding | None,
    evidence: tuple[Evidence, ...],
    project: ProjectRef,
) -> TimelineCorrelation | None:
    """Correlate release events and time-window ops signals without store access."""

    if ops_finding is None or not ops_finding.signals:
        return None
    release_events = tuple(
        event
        for event in timeline
        if event.event_type == "release" and _same_service(event, project)
    )
    if not release_events:
        return None
    first_signal_at = min(signal.observed_at for signal in ops_finding.signals)
    nearest_release = min(
        release_events,
        key=lambda event: abs(_delta_minutes(first_signal_at, event.observed_at)),
    )
    signed_delta = _delta_minutes(first_signal_at, nearest_release.observed_at)
    abs_delta = abs(signed_delta)
    ops_after_release = signed_delta >= 0
    release_id = _release_id(nearest_release)
    facts = _facts(
        release_id=release_id,
        release_at=nearest_release.observed_at,
        ops_at=first_signal_at,
        delta_minutes=abs_delta,
        ops_after_release=ops_after_release,
    )
    inferences = _inferences(
        release_id=release_id,
        delta_minutes=abs_delta,
        ops_after_release=ops_after_release,
    )
    hypotheses = _hypotheses(ops_after_release)
    recommendations = _recommendations(ops_after_release)
    evidence_ids = _evidence_ids(nearest_release, ops_finding, evidence)
    return TimelineCorrelation(
        ordered_events=_ordered_events(release_events, ops_finding),
        nearest_release_id=release_id,
        nearest_release_delta_minutes=abs_delta,
        ops_after_release=ops_after_release,
        facts=facts,
        inferences=inferences,
        hypotheses=hypotheses,
        recommendations=recommendations,
        evidence_ids=evidence_ids,
    )


def _same_service(event: TimelineEvent, project: ProjectRef) -> bool:
    if project.service is None:
        return True
    return project.service in event.references or project.service in event.summary


def _delta_minutes(left: datetime, right: datetime) -> int:
    return round((left - right).total_seconds() / 60)


def _release_id(event: TimelineEvent) -> str:
    for ref in event.references:
        if ref.startswith("REL-"):
            return ref
    return event.source.source_id


def _facts(
    *,
    release_id: str,
    release_at: datetime,
    ops_at: datetime,
    delta_minutes: int,
    ops_after_release: bool,
) -> tuple[str, ...]:
    direction = "after" if ops_after_release else "before"
    return (
        (
            f"ProjectOps timeline correlation: nearest release {release_id} "
            f"at {release_at.isoformat()}."
        ),
        (
            f"ProjectOps timeline correlation: first ops signal at {ops_at.isoformat()}, "
            f"{delta_minutes} minutes {direction} nearest release."
        ),
    )


def _inferences(
    *,
    release_id: str,
    delta_minutes: int,
    ops_after_release: bool,
) -> tuple[str, ...]:
    if ops_after_release:
        closeness = (
            "within_near_release_window"
            if delta_minutes <= NEAR_RELEASE_WINDOW_MINUTES
            else "outside_near_release_window"
        )
        return (
            (
                f"ProjectOps timeline correlation inference: ops_after_release "
                f"for {release_id}; {closeness}; investigate whether the release "
                "introduced or exposed the anomaly."
            ),
        )
    return (
        (
            f"ProjectOps timeline correlation inference: release_after_ops for {release_id}; "
            "the release is more likely a follow-up hotfix or later mitigation than "
            "the initial trigger."
        ),
    )


def _hypotheses(ops_after_release: bool) -> tuple[str, ...]:
    if ops_after_release:
        return (
            (
                "ProjectOps hypothesis: temporal proximity is not causality; confirm "
                "with trace-level reproduction, rollout metadata, and commit diff."
            ),
        )
    return (
        (
            "ProjectOps hypothesis: the anomaly likely started before the nearest "
            "release; confirm with earlier logs, incident notes, and rollback/deploy records."
        ),
    )


def _recommendations(ops_after_release: bool) -> tuple[str, ...]:
    if ops_after_release:
        return (
            (
                "Read-only next step: compare the release commit diff with the affected "
                "route, then reproduce before proposing rollback or code changes."
            ),
        )
    return (
        (
            "Read-only next step: treat the nearest release as a candidate mitigation, "
            "then inspect pre-release signals to find the first bad event."
        ),
    )


def _evidence_ids(
    release: TimelineEvent,
    ops_finding: OpsFinding,
    evidence: tuple[Evidence, ...],
) -> tuple[UUID, ...]:
    known = {item.id for item in evidence}
    ids = [release.evidence_id]
    ids.extend(item.id for item in ops_finding.evidence)
    return tuple(dict.fromkeys(item for item in ids if item in known))


def _ordered_events(
    releases: tuple[TimelineEvent, ...],
    ops_finding: OpsFinding,
) -> tuple[str, ...]:
    events = [
        (event.observed_at, f"release:{_release_id(event)}:{event.observed_at.isoformat()}")
        for event in releases
    ]
    events.extend(
        (
            signal.observed_at,
            f"ops:{signal.kind.value}:{signal.observed_at.isoformat()}",
        )
        for signal in ops_finding.signals
    )
    return tuple(label for _, label in sorted(events, key=lambda item: item[0]))
