"""Risk persistence ports and in-memory/SQLite implementations."""

from __future__ import annotations

import sqlite3
from typing import Protocol

from project_lens.domain.models import ProjectRef
from project_lens.domain.risk import RiskFinding, RiskStateEvent


class RiskStore(Protocol):
    def get(self, risk_id: str) -> RiskFinding | None: ...

    def save(self, finding: RiskFinding) -> None: ...

    def list_for_project(self, project: ProjectRef) -> tuple[RiskFinding, ...]: ...

    def append_event(self, event: RiskStateEvent) -> None: ...

    def events_for_risk(self, risk_id: str) -> tuple[RiskStateEvent, ...]: ...


class SQLiteRiskDatabase(Protocol):
    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor: ...

    def query_one(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> sqlite3.Row | None: ...

    def query_all(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> list[sqlite3.Row]: ...


class InMemoryRiskStore:
    def __init__(self) -> None:
        self._findings: dict[str, RiskFinding] = {}
        self._events: list[RiskStateEvent] = []

    def get(self, risk_id: str) -> RiskFinding | None:
        return self._findings.get(risk_id)

    def save(self, finding: RiskFinding) -> None:
        self._findings[finding.risk_id] = finding

    def list_for_project(self, project: ProjectRef) -> tuple[RiskFinding, ...]:
        return tuple(
            sorted(
                (
                    finding
                    for finding in self._findings.values()
                    if finding.project.tenant_id == project.tenant_id
                    and finding.project.project_id == project.project_id
                    and (project.service is None or finding.project.service == project.service)
                    and (
                        project.environment is None
                        or finding.project.environment == project.environment
                    )
                ),
                key=lambda item: (item.detected_at, item.risk_id),
            )
        )

    def append_event(self, event: RiskStateEvent) -> None:
        self._events.append(event)

    def events_for_risk(self, risk_id: str) -> tuple[RiskStateEvent, ...]:
        return tuple(event for event in self._events if event.risk_id == risk_id)


class SQLiteRiskStore:
    def __init__(self, database: SQLiteRiskDatabase) -> None:
        self._database = database

    def get(self, risk_id: str) -> RiskFinding | None:
        row = self._database.query_one(
            "SELECT payload FROM risk_findings WHERE risk_id = ?",
            (risk_id,),
        )
        return RiskFinding.model_validate_json(row["payload"]) if row else None

    def save(self, finding: RiskFinding) -> None:
        self._database.execute(
            "INSERT OR REPLACE INTO risk_findings (risk_id, tenant_id, project_id, payload) "
            "VALUES (?, ?, ?, ?)",
            (
                finding.risk_id,
                finding.project.tenant_id,
                finding.project.project_id,
                finding.model_dump_json(),
            ),
        )

    def list_for_project(self, project: ProjectRef) -> tuple[RiskFinding, ...]:
        rows = self._database.query_all(
            "SELECT payload FROM risk_findings WHERE tenant_id = ? AND project_id = ?",
            (project.tenant_id, project.project_id),
        )
        findings = tuple(RiskFinding.model_validate_json(row["payload"]) for row in rows)
        return tuple(
            sorted(
                (
                    finding
                    for finding in findings
                    if (project.service is None or finding.project.service == project.service)
                    and (
                        project.environment is None
                        or finding.project.environment == project.environment
                    )
                ),
                key=lambda item: (item.detected_at, item.risk_id),
            )
        )

    def append_event(self, event: RiskStateEvent) -> None:
        self._database.execute(
            "INSERT INTO risk_state_events (risk_id, payload) VALUES (?, ?)",
            (event.risk_id, event.model_dump_json()),
        )

    def events_for_risk(self, risk_id: str) -> tuple[RiskStateEvent, ...]:
        rows = self._database.query_all(
            "SELECT payload FROM risk_state_events WHERE risk_id = ? ORDER BY sequence",
            (risk_id,),
        )
        return tuple(RiskStateEvent.model_validate_json(row["payload"]) for row in rows)
