"""Idempotent in-process review queue; persistence adapters can wrap the same contract."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from project_lens.domain.memory import ReviewReason
from project_lens.domain.memory_review import MemoryReview, MemoryReviewStatus
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


class MemoryReviewQueue:
    def __init__(self) -> None:
        self._items: dict[UUID, MemoryReview] = {}
        self._by_trigger: dict[tuple[UUID, ReviewReason, str], UUID] = {}

    def open(
        self,
        *,
        memory_id: UUID,
        project: ProjectRef,
        reason: ReviewReason,
        trigger_key: str,
        due_at: datetime | None = None,
    ) -> MemoryReview:
        key = (memory_id, reason, trigger_key)
        existing_id = self._by_trigger.get(key)
        if existing_id is not None:
            return self._items[existing_id]
        review = MemoryReview(
            memory_id=memory_id,
            project=project,
            reason=reason,
            trigger_key=trigger_key,
            due_at=due_at,
        )
        self._items[review.review_id] = review
        self._by_trigger[key] = review.review_id
        return review

    def list(self, *, project: ProjectRef, status: MemoryReviewStatus | None = None) -> tuple[MemoryReview, ...]:
        return tuple(
            item for item in self._items.values()
            if item.project == project and (status is None or item.status is status)
        )

    def resolve(self, review_id: UUID, *, resolved_by: str, resolution: str, status: MemoryReviewStatus = MemoryReviewStatus.RESOLVED) -> MemoryReview:
        current = self._items.get(review_id)
        if current is None:
            raise KeyError(review_id)
        updated = current.model_copy(update={
            "status": status,
            "resolved_by": resolved_by,
            "resolution": resolution,
            "resolved_at": datetime.now(current.opened_at.tzinfo),
        })
        self._items[review_id] = updated
        return updated


class SQLiteMemoryReviewQueue:
    """Durable review queue backed by the namespaced memory schema."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._database.execute(
            """CREATE TABLE IF NOT EXISTS memory_reviews (
                review_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                reason TEXT NOT NULL, status TEXT NOT NULL,
                trigger_key TEXT NOT NULL, opened_at TEXT NOT NULL,
                due_at TEXT, resolved_at TEXT, resolved_by TEXT,
                resolution TEXT, payload TEXT NOT NULL)"""
        )
        self._database.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_reviews_trigger "
            "ON memory_reviews (memory_id, reason, trigger_key)"
        )

    def open(
        self,
        *,
        memory_id: UUID,
        project: ProjectRef,
        reason: ReviewReason,
        trigger_key: str,
        due_at: datetime | None = None,
    ) -> MemoryReview:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload FROM memory_reviews WHERE memory_id = ? AND reason = ? AND trigger_key = ?",
                (str(memory_id), reason.value, trigger_key),
            ).fetchone()
            if row is not None:
                return MemoryReview.model_validate_json(row["payload"])
            review = MemoryReview(
                memory_id=memory_id,
                project=project,
                reason=reason,
                trigger_key=trigger_key,
                due_at=due_at,
            )
            connection.execute(
                """INSERT INTO memory_reviews
                (review_id, memory_id, tenant_id, project_id, reason, status,
                 trigger_key, opened_at, due_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(review.review_id), str(review.memory_id), project.tenant_id,
                    project.project_id, review.reason.value, review.status.value,
                    review.trigger_key, review.opened_at.isoformat(),
                    review.due_at.isoformat() if review.due_at else None,
                    review.model_dump_json(),
                ),
            )
            return review

    def list(self, *, project: ProjectRef, status: MemoryReviewStatus | None = None) -> tuple[MemoryReview, ...]:
        query = "SELECT payload FROM memory_reviews WHERE tenant_id = ? AND project_id = ?"
        params: tuple[object, ...] = (project.tenant_id, project.project_id)
        if status is not None:
            query += " AND status = ?"
            params += (status.value,)
        query += " ORDER BY opened_at, review_id"
        return tuple(
            MemoryReview.model_validate_json(row["payload"])
            for row in self._database.query_all(query, params)
        )

    def resolve(
        self,
        review_id: UUID,
        *,
        resolved_by: str,
        resolution: str,
        status: MemoryReviewStatus = MemoryReviewStatus.RESOLVED,
    ) -> MemoryReview:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload FROM memory_reviews WHERE review_id = ?", (str(review_id),)
            ).fetchone()
            if row is None:
                raise KeyError(review_id)
            current = MemoryReview.model_validate_json(row["payload"])
            updated = current.model_copy(update={
                "status": status,
                "resolved_by": resolved_by,
                "resolution": resolution,
                "resolved_at": datetime.now(current.opened_at.tzinfo),
            })
            connection.execute(
                """UPDATE memory_reviews SET status = ?, resolved_at = ?,
                    resolved_by = ?, resolution = ?, payload = ? WHERE review_id = ?""",
                (
                    updated.status.value,
                    updated.resolved_at.isoformat() if updated.resolved_at else None,
                    updated.resolved_by,
                    updated.resolution,
                    updated.model_dump_json(),
                    str(review_id),
                ),
            )
            return updated
