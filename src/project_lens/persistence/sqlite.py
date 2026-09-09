"""SQLite adapters for durable runs, events, and approval requests."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from threading import Lock
from uuid import UUID, uuid4

from project_lens.domain.models import AgentRun
from project_lens.domain.approval import ApprovalRecord, ApprovalStatus as EngineeringApprovalStatus
from project_lens.integrations.feishu.approvals import ApprovalRequest, ApprovalStatus
from project_lens.runtime.events import AgentEvent, AgentEventType


class SQLiteDatabase:
    def __init__(self, path: str) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                project_id TEXT,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS engineering_approvals (
                approval_id TEXT PRIMARY KEY,
                project_tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                proposal_id TEXT,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_engineering_approvals_project
                ON engineering_approvals (project_tenant_id, project_id);
            CREATE TABLE IF NOT EXISTS feishu_events (
                event_id TEXT PRIMARY KEY,
                first_seen_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_findings (
                risk_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_risk_findings_project
                ON risk_findings (tenant_id, project_id);
            CREATE TABLE IF NOT EXISTS risk_state_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                risk_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_risk_events_risk
                ON risk_state_events (risk_id, sequence);
            CREATE TABLE IF NOT EXISTS risk_feedback (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                risk_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_risk_feedback_project
                ON risk_feedback (tenant_id, project_id, risk_id, sequence);
            CREATE TABLE IF NOT EXISTS risk_notifications (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                risk_id TEXT,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hermes_risk_reviews (
                idempotency_key TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_risk_notifications_project
                ON risk_notifications (tenant_id, project_id, sequence);
            """
        )
        self._ensure_run_scope_columns()
        self._connection.commit()

    def _ensure_run_scope_columns(self) -> None:
        """Migrate databases created before runs carried indexed scope columns."""

        columns = {str(row["name"]) for row in self._connection.execute("PRAGMA table_info(runs)").fetchall()}
        if "tenant_id" not in columns:
            self._connection.execute("ALTER TABLE runs ADD COLUMN tenant_id TEXT")
        if "project_id" not in columns:
            self._connection.execute("ALTER TABLE runs ADD COLUMN project_id TEXT")
        # Backfill rows written by pre-index versions. This is intentionally
        # bounded to rows missing either scope column and runs before serving
        # queries, so project filtering remains SQL-side afterwards.
        legacy_rows = self._connection.execute(
            "SELECT id, payload FROM runs WHERE tenant_id IS NULL OR project_id IS NULL"
        ).fetchall()
        for row in legacy_rows:
            try:
                run = AgentRun.model_validate_json(row["payload"])
            except Exception:
                continue
            self._connection.execute(
                "UPDATE runs SET tenant_id = ?, project_id = ? WHERE id = ?",
                (run.project.tenant_id, run.project.project_id, row["id"]),
            )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_project_updated ON runs (tenant_id, project_id)"
        )

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            self._connection.commit()
            return cursor

    def query_one(self, sql: str, parameters: tuple[object, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchone()

    def query_all(self, sql: str, parameters: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, parameters).fetchall())


class SQLiteRunRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def add(self, run: AgentRun) -> None:
        self._database.execute(
            "INSERT OR REPLACE INTO runs (id, tenant_id, project_id, payload) VALUES (?, ?, ?, ?)",
            (str(run.id), run.project.tenant_id, run.project.project_id, run.model_dump_json()),
        )

    def get(self, run_id: UUID) -> AgentRun | None:
        row = self._database.query_one("SELECT payload FROM runs WHERE id = ?", (str(run_id),))
        return AgentRun.model_validate_json(row["payload"]) if row else None

    def list_recent(self, project, *, limit: int = 100):
        bounded = max(1, int(limit))
        rows = self._database.query_all(
            "SELECT payload FROM runs WHERE tenant_id = ? AND project_id = ?",
            (project.tenant_id, project.project_id),
        )
        runs = [AgentRun.model_validate_json(row["payload"]) for row in rows]
        runs.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(runs[:bounded])


class SQLiteEventSink:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    async def emit(self, event: AgentEvent) -> None:
        self._database.execute(
            "INSERT INTO agent_events (run_id, payload) VALUES (?, ?)",
            (str(event.run_id), _event_json(event)),
        )

    def for_run(self, run_id: UUID) -> tuple[AgentEvent, ...]:
        rows = self._database.query_all(
            "SELECT payload FROM agent_events WHERE run_id = ? ORDER BY sequence",
            (str(run_id),),
        )
        return tuple(_event_from_json(row["payload"]) for row in rows)


class SQLiteApprovalStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def create(self, *, run_id: UUID, action_id: UUID, requested_by: str) -> ApprovalRequest:
        request = ApprovalRequest(
            id=uuid4(),
            run_id=run_id,
            action_id=action_id,
            requested_by=requested_by,
        )
        self._save(request)
        return request

    def get(self, approval_id: UUID) -> ApprovalRequest | None:
        row = self._database.query_one(
            "SELECT payload FROM approvals WHERE id = ?", (str(approval_id),)
        )
        return ApprovalRequest.model_validate_json(row["payload"]) if row else None

    def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str,
    ) -> ApprovalRequest | None:
        current = self.get(approval_id)
        if current is None:
            return None
        if current.status != ApprovalStatus.PENDING:
            raise ValueError(f"approval is already {current.status.value}")
        updated = current.model_copy(
            update={
                "status": ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
                "decided_by": decided_by,
                "decided_at": datetime.now(current.created_at.tzinfo),
            }
        )
        self._save(updated)
        return updated

    def _save(self, request: ApprovalRequest) -> None:
        self._database.execute(
            "INSERT OR REPLACE INTO approvals (id, payload) VALUES (?, ?)",
            (str(request.id), request.model_dump_json()),
        )


class SQLiteEngineeringApprovalStore:
    """Durable approval records for Hermes engineering proposals."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def create(self, approval: ApprovalRecord) -> ApprovalRecord:
        self._database.execute(
            "INSERT INTO engineering_approvals (approval_id, project_tenant_id, project_id, proposal_id, payload) VALUES (?, ?, ?, ?, ?)",
            (str(approval.approval_id), approval.project.tenant_id, approval.project.project_id, str(approval.proposal_id) if approval.proposal_id else None, approval.model_dump_json()),
        )
        return approval

    def get(self, approval_id: UUID) -> ApprovalRecord | None:
        row = self._database.query_one("SELECT payload FROM engineering_approvals WHERE approval_id = ?", (str(approval_id),))
        return ApprovalRecord.model_validate_json(row["payload"]) if row else None

    def decide(self, approval_id: UUID, *, approved: bool, decided_by: str) -> ApprovalRecord | None:
        current = self.get(approval_id)
        if current is None:
            return None
        if current.decision is not EngineeringApprovalStatus.PENDING:
            raise ValueError(f"approval is already {current.decision.value}")
        if current.expires_at is not None:
            expires = current.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                expired = current.model_copy(update={"decision": EngineeringApprovalStatus.EXPIRED, "decided_at": datetime.now(timezone.utc)})
                self._database.execute("UPDATE engineering_approvals SET payload = ? WHERE approval_id = ?", (expired.model_dump_json(), str(approval_id)))
                raise ValueError("approval has expired")
        updated = current.model_copy(update={"decision": EngineeringApprovalStatus.APPROVED if approved else EngineeringApprovalStatus.REJECTED, "decided_by": decided_by, "decided_at": datetime.now(timezone.utc)})
        self._database.execute("UPDATE engineering_approvals SET payload = ? WHERE approval_id = ?", (updated.model_dump_json(), str(approval_id)))
        return updated


class SQLiteEventDeduplicator:
    """Atomically remember Feishu event IDs across process restarts."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def mark_seen(self, event_id: str) -> bool:
        cursor = self._database.execute(
            "INSERT OR IGNORE INTO feishu_events (event_id, first_seen_at) VALUES (?, ?)",
            (event_id, datetime.now(timezone.utc).isoformat()),
        )
        return cursor.rowcount == 1


def _event_json(event: AgentEvent) -> str:
    return json.dumps(
        {
            "run_id": str(event.run_id),
            "trace_id": str(event.trace_id),
            "type": event.type.value,
            "occurred_at": event.occurred_at.isoformat(),
            "payload": event.payload,
        },
        ensure_ascii=False,
    )


def _event_from_json(payload: str) -> AgentEvent:
    data = json.loads(payload)
    return AgentEvent(
        run_id=UUID(data["run_id"]),
        trace_id=UUID(data["trace_id"]),
        type=AgentEventType(data["type"]),
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
        payload=data["payload"],
    )
