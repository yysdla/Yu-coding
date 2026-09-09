"""Persistence ports for risk feedback and notification idempotency."""

from __future__ import annotations

import sqlite3
from typing import Protocol

from project_lens.domain.models import ProjectRef
from project_lens.domain.risk_feedback import RiskFeedback, RiskNotificationRecord


class HermesRiskReviewStore(Protocol):
    def claim_review(self, idempotency_key: str, project: ProjectRef) -> bool: ...


class RiskFeedbackStore(Protocol):
    def create_feedback(self, feedback: RiskFeedback) -> tuple[RiskFeedback, bool]: ...

    def get_feedback_by_key(self, idempotency_key: str) -> RiskFeedback | None: ...

    def list_feedback(
        self,
        project: ProjectRef,
        *,
        risk_id: str | None = None,
    ) -> tuple[RiskFeedback, ...]: ...

    def mark_notification(self, record: RiskNotificationRecord) -> bool: ...

    def notification_seen(self, idempotency_key: str) -> bool: ...

    def delete_notification(self, idempotency_key: str) -> None: ...

    def list_notifications(
        self,
        project: ProjectRef,
    ) -> tuple[RiskNotificationRecord, ...]: ...


class InMemoryRiskFeedbackStore:
    def __init__(self) -> None:
        self._feedback: dict[str, RiskFeedback] = {}
        self._notifications: dict[str, RiskNotificationRecord] = {}

    def create_feedback(self, feedback: RiskFeedback) -> tuple[RiskFeedback, bool]:
        current = self._feedback.get(feedback.idempotency_key)
        if current is not None:
            return current, False
        self._feedback[feedback.idempotency_key] = feedback
        return feedback, True

    def get_feedback_by_key(self, idempotency_key: str) -> RiskFeedback | None:
        return self._feedback.get(idempotency_key)

    def list_feedback(
        self,
        project: ProjectRef,
        *,
        risk_id: str | None = None,
    ) -> tuple[RiskFeedback, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._feedback.values()
                    if item.project.tenant_id == project.tenant_id
                    and item.project.project_id == project.project_id
                    and (risk_id is None or item.risk_id == risk_id)
                ),
                key=lambda item: (item.created_at, str(item.feedback_id)),
            )
        )

    def mark_notification(self, record: RiskNotificationRecord) -> bool:
        if record.idempotency_key in self._notifications:
            return False
        self._notifications[record.idempotency_key] = record
        return True

    def notification_seen(self, idempotency_key: str) -> bool:
        return idempotency_key in self._notifications

    def delete_notification(self, idempotency_key: str) -> None:
        self._notifications.pop(idempotency_key, None)

    def list_notifications(self, project: ProjectRef) -> tuple[RiskNotificationRecord, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._notifications.values()
                    if item.project.tenant_id == project.tenant_id
                    and item.project.project_id == project.project_id
                ),
                key=lambda item: (item.sent_at, str(item.notification_id)),
            )
        )


class InMemoryHermesRiskReviewStore:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    def claim_review(self, idempotency_key: str, project: ProjectRef) -> bool:
        if idempotency_key in self._keys:
            return False
        self._keys.add(idempotency_key)
        return True


class SQLiteRiskFeedbackDatabase(Protocol):
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


class SQLiteRiskFeedbackStore:
    def __init__(self, database: SQLiteRiskFeedbackDatabase) -> None:
        self._database = database

    def create_feedback(self, feedback: RiskFeedback) -> tuple[RiskFeedback, bool]:
        cursor = self._database.execute(
            "INSERT OR IGNORE INTO risk_feedback "
            "(idempotency_key, tenant_id, project_id, risk_id, payload) VALUES (?, ?, ?, ?, ?)",
            (
                feedback.idempotency_key,
                feedback.project.tenant_id,
                feedback.project.project_id,
                feedback.risk_id,
                feedback.model_dump_json(),
            ),
        )
        if cursor.rowcount == 1:
            return feedback, True
        current = self.get_feedback_by_key(feedback.idempotency_key)
        if current is None:  # pragma: no cover - defensive database consistency check
            raise RuntimeError("risk feedback insert was ignored but no record exists")
        return current, False

    def get_feedback_by_key(self, idempotency_key: str) -> RiskFeedback | None:
        row = self._database.query_one(
            "SELECT payload FROM risk_feedback WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return RiskFeedback.model_validate_json(row["payload"]) if row else None

    def list_feedback(
        self,
        project: ProjectRef,
        *,
        risk_id: str | None = None,
    ) -> tuple[RiskFeedback, ...]:
        if risk_id is None:
            rows = self._database.query_all(
                "SELECT payload FROM risk_feedback WHERE tenant_id = ? AND project_id = ? "
                "ORDER BY sequence",
                (project.tenant_id, project.project_id),
            )
        else:
            rows = self._database.query_all(
                "SELECT payload FROM risk_feedback WHERE tenant_id = ? AND project_id = ? "
                "AND risk_id = ? ORDER BY sequence",
                (project.tenant_id, project.project_id, risk_id),
            )
        return tuple(RiskFeedback.model_validate_json(row["payload"]) for row in rows)

    def mark_notification(self, record: RiskNotificationRecord) -> bool:
        cursor = self._database.execute(
            "INSERT OR IGNORE INTO risk_notifications "
            "(idempotency_key, tenant_id, project_id, risk_id, payload) VALUES (?, ?, ?, ?, ?)",
            (
                record.idempotency_key,
                record.project.tenant_id,
                record.project.project_id,
                record.risk_id,
                record.model_dump_json(),
            ),
        )
        return cursor.rowcount == 1

    def notification_seen(self, idempotency_key: str) -> bool:
        return self._database.query_one(
            "SELECT 1 FROM risk_notifications WHERE idempotency_key = ?",
            (idempotency_key,),
        ) is not None

    def delete_notification(self, idempotency_key: str) -> None:
        self._database.execute(
            "DELETE FROM risk_notifications WHERE idempotency_key = ?",
            (idempotency_key,),
        )

    def list_notifications(self, project: ProjectRef) -> tuple[RiskNotificationRecord, ...]:
        rows = self._database.query_all(
            "SELECT payload FROM risk_notifications WHERE tenant_id = ? AND project_id = ? "
            "ORDER BY sequence",
            (project.tenant_id, project.project_id),
        )
        return tuple(RiskNotificationRecord.model_validate_json(row["payload"]) for row in rows)


class SQLiteHermesRiskReviewStore:
    def __init__(self, database: SQLiteRiskFeedbackDatabase) -> None:
        self._database = database

    def claim_review(self, idempotency_key: str, project: ProjectRef) -> bool:
        cursor = self._database.execute(
            "INSERT OR IGNORE INTO hermes_risk_reviews (idempotency_key, tenant_id, project_id) VALUES (?, ?, ?)",
            (idempotency_key, project.tenant_id, project.project_id),
        )
        return cursor.rowcount == 1
