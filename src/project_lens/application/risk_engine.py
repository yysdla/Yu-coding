"""Risk rule orchestration, deduplication, and lifecycle transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from project_lens.application.risk_rules import (
    RiskRule,
    RiskRuleConfig,
    default_risk_rules,
)
from project_lens.application.risk_store import RiskStore
from project_lens.domain.models import Evidence, ProjectRef
from project_lens.domain.risk import (
    RiskEventType,
    RiskFinding,
    RiskSeverity,
    RiskState,
    RiskStateEvent,
    ensure_utc,
    evidence_signature,
    severity_rank,
)


@dataclass(frozen=True)
class RiskScanResult:
    findings: tuple[RiskFinding, ...]
    events: tuple[RiskStateEvent, ...]
    notify_risk_ids: tuple[str, ...]

    @property
    def open_findings(self) -> tuple[RiskFinding, ...]:
        return tuple(finding for finding in self.findings if finding.state == RiskState.OPEN)


class RiskEngine:
    def __init__(
        self,
        store: RiskStore,
        *,
        rules: tuple[RiskRule, ...] | None = None,
        config: RiskRuleConfig | None = None,
    ) -> None:
        self._store = store
        self._rules = rules or default_risk_rules()
        self._config = config or RiskRuleConfig()

    def get(self, risk_id: str) -> RiskFinding | None:
        return self._store.get(risk_id)

    def list_for_project(self, project: ProjectRef) -> tuple[RiskFinding, ...]:
        return self._store.list_for_project(project)

    def scan(
        self,
        project: ProjectRef,
        evidence: tuple[Evidence, ...],
        *,
        now: datetime | None = None,
        complete_snapshot: bool = True,
    ) -> RiskScanResult:
        observed_at = ensure_utc(now or datetime.now(timezone.utc))
        project_evidence = tuple(
            item
            for item in evidence
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
            and (project.service is None or item.project.service in {None, project.service})
            and (
                project.environment is None
                or item.project.environment in {None, project.environment}
            )
        )
        evidence_by_id = {item.id: item for item in project_evidence}
        candidates = [
            finding
            for rule in self._rules
            for finding in rule(project_evidence, observed_at, self._config)
        ]
        candidates_by_id = self._coalesce(candidates, evidence_by_id, observed_at)
        existing = {item.risk_id: item for item in self._store.list_for_project(project)}
        events: list[RiskStateEvent] = []
        notify: list[str] = []

        for risk_id, candidate in candidates_by_id.items():
            current = existing.get(risk_id)
            if current is None:
                self._store.save(candidate)
                event = _event(
                    candidate,
                    RiskEventType.DETECTED,
                    observed_at,
                    previous_state=None,
                    previous_severity=None,
                )
                self._record(event, events)
                notify.append(risk_id)
                continue

            updated = candidate.model_copy(
                update={
                    "detected_at": current.detected_at,
                    "last_seen_at": observed_at,
                    "state": current.state,
                    "resolved_at": current.resolved_at,
                }
            )
            if current.state == RiskState.RESOLVED:
                updated = updated.model_copy(
                    update={"state": RiskState.OPEN, "resolved_at": None}
                )
                self._store.save(updated)
                event = _event(
                    updated,
                    RiskEventType.STATE_CHANGED,
                    observed_at,
                    previous_state=RiskState.RESOLVED,
                    previous_severity=current.severity,
                    details={"reason": "evidence_reappeared"},
                )
                self._record(event, events)
                notify.append(risk_id)
                continue

            severity_increased = severity_rank(updated.severity) > severity_rank(current.severity)
            evidence_changed = updated.evidence_signature != current.evidence_signature
            material_changed = evidence_changed or (
                updated.title,
                updated.summary,
                updated.owner_ids,
                updated.affected_refs,
            ) != (
                current.title,
                current.summary,
                current.owner_ids,
                current.affected_refs,
            )
            self._store.save(updated)
            if severity_increased:
                event = _event(
                    updated,
                    RiskEventType.SEVERITY_CHANGED,
                    observed_at,
                    previous_state=current.state,
                    previous_severity=current.severity,
                )
                self._record(event, events)
                notify.append(risk_id)
            elif material_changed:
                event = _event(
                    updated,
                    RiskEventType.UPDATED,
                    observed_at,
                    previous_state=current.state,
                    previous_severity=current.severity,
                )
                self._record(event, events)
                notify.append(risk_id)

        if complete_snapshot:
            resolvable = existing.items()
        else:
            resolvable = (
                (risk_id, current)
                for risk_id, current in existing.items()
                if _snapshot_covers(current, project_evidence)
            )
        for risk_id, current in resolvable:
            if risk_id in candidates_by_id or current.state == RiskState.RESOLVED:
                continue
            resolved = current.model_copy(
                update={
                    "state": RiskState.RESOLVED,
                    "last_seen_at": observed_at,
                    "resolved_at": observed_at,
                }
            )
            self._store.save(resolved)
            event = _event(
                resolved,
                RiskEventType.RESOLVED,
                observed_at,
                previous_state=current.state,
                previous_severity=current.severity,
                details={"reason": "supporting_evidence_no_longer_matches"},
            )
            self._record(event, events)

        return RiskScanResult(
            findings=self._store.list_for_project(project),
            events=tuple(events),
            notify_risk_ids=tuple(dict.fromkeys(notify)),
        )

    def transition(
        self,
        risk_id: str,
        state: RiskState,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> RiskFinding:
        current = self._store.get(risk_id)
        if current is None:
            raise KeyError(f"risk not found: {risk_id}")
        observed_at = ensure_utc(now or datetime.now(timezone.utc))
        if current.state == state:
            return current
        updated = current.model_copy(
            update={
                "state": state,
                "last_seen_at": observed_at,
                "resolved_at": observed_at if state == RiskState.RESOLVED else None,
            }
        )
        self._store.save(updated)
        event_type = (
            RiskEventType.RESOLVED
            if state == RiskState.RESOLVED
            else RiskEventType.STATE_CHANGED
        )
        event = _event(
            updated,
            event_type,
            observed_at,
            previous_state=current.state,
            previous_severity=current.severity,
            actor_id=actor_id,
            details={"reason": "actor_feedback"},
        )
        self._store.append_event(event)
        return updated

    def _coalesce(
        self,
        candidates: list[RiskFinding],
        evidence_by_id: dict[UUID, Evidence],
        now: datetime,
    ) -> dict[str, RiskFinding]:
        merged: dict[str, RiskFinding] = {}
        for candidate in candidates:
            if candidate.project.tenant_id == "" or candidate.project.project_id == "":
                continue
            if not candidate.evidence_ids or any(
                evidence_id not in evidence_by_id for evidence_id in candidate.evidence_ids
            ):
                raise ValueError(
                    f"risk {candidate.risk_id} references evidence outside the authorized scan set"
                )
            current = merged.get(candidate.risk_id)
            if current is None:
                merged[candidate.risk_id] = candidate
                continue
            evidence_ids = tuple(dict.fromkeys((*current.evidence_ids, *candidate.evidence_ids)))
            severity = (
                candidate.severity
                if severity_rank(candidate.severity) > severity_rank(current.severity)
                else current.severity
            )
            merged[candidate.risk_id] = current.model_copy(
                update={
                    "severity": severity,
                    "summary": " ".join(dict.fromkeys((current.summary, candidate.summary))),
                    "affected_refs": tuple(
                        dict.fromkeys((*current.affected_refs, *candidate.affected_refs))
                    ),
                    "evidence_ids": evidence_ids,
                    "evidence_signature": evidence_signature(
                        [
                            (evidence_id, evidence_by_id[evidence_id].content_hash)
                            for evidence_id in evidence_ids
                        ]
                    ),
                    "last_seen_at": now,
                }
            )
        return merged

    def _record(self, event: RiskStateEvent, events: list[RiskStateEvent]) -> None:
        self._store.append_event(event)
        events.append(event)


def _event(
    finding: RiskFinding,
    event_type: RiskEventType,
    occurred_at: datetime,
    *,
    previous_state: RiskState | None,
    previous_severity: RiskSeverity | None,
    actor_id: str | None = None,
    details: dict[str, object] | None = None,
) -> RiskStateEvent:
    return RiskStateEvent(
        risk_id=finding.risk_id,
        event_type=event_type,
        occurred_at=occurred_at,
        previous_state=previous_state,
        current_state=finding.state,
        previous_severity=previous_severity,
        current_severity=finding.severity,
        evidence_ids=finding.evidence_ids,
        actor_id=actor_id,
        details=details or {},
    )


def _snapshot_covers(finding: RiskFinding, evidence: tuple[Evidence, ...]) -> bool:
    refs = {finding.primary_ref, *finding.affected_refs}
    for item in evidence:
        if item.source.source_id in refs:
            return True
        for key in (
            "task_id",
            "requirement_id",
            "pr_id",
            "number",
            "message_id",
            "commit_sha",
        ):
            value = item.metadata.get(key)
            if value is not None and str(value) in refs:
                return True
    return False
