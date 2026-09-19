"""Application orchestration for opening and resolving memory reviews."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from project_lens.context.memory_review_queue import MemoryReviewQueue
from project_lens.context.memory_store import MemoryStore
from project_lens.domain.memory import ReviewReason
from project_lens.domain.memory_review import MemoryReview
from project_lens.domain.models import ProjectRef, utc_now


class MemoryReviewService:
    def __init__(self, store: MemoryStore, queue: MemoryReviewQueue | None = None) -> None:
        self.store = store
        self.queue = queue or MemoryReviewQueue()

    def list_reviews(self, *, project: ProjectRef, status=None):
        return self.queue.list(project=project, status=status)

    def resolve_review(self, review_id: UUID, *, resolved_by: str, resolution: str, status=None):
        kwargs = {"resolved_by": resolved_by, "resolution": resolution}
        if status is not None:
            kwargs["status"] = status
        return self.queue.resolve(review_id, **kwargs)

    def open_review_for_source_revision(self, source_id: str, old_revision: str, new_revision: str, *, project: ProjectRef | None = None) -> tuple[MemoryReview, ...]:
        if project is None:
            return ()
        opened: list[MemoryReview] = []
        for memory in self.store.list_memories(project, include_inactive=True):
            if any(ref.source_id == source_id and ref.revision == old_revision for ref in memory.source_refs):
                opened.append(self.queue.open(
                    memory_id=memory.id,
                    project=memory.project,
                    reason=ReviewReason.SOURCE_REVISED,
                    trigger_key=f"source:{source_id}:{new_revision}",
                ))
        return tuple(opened)

    def open_review_for_revoked_evidence(self, evidence_id: UUID, *, project: ProjectRef | None = None) -> tuple[MemoryReview, ...]:
        if project is None:
            return ()
        return tuple(
            self.queue.open(
                memory_id=memory.id,
                project=memory.project,
                reason=ReviewReason.EVIDENCE_REVOKED,
                trigger_key=f"evidence:{evidence_id}",
            )
            for memory in self.store.list_memories(project, include_inactive=True)
            if evidence_id in memory.evidence_ids
        )

    def open_review_for_source_revoked(self, source_id: str, *, project: ProjectRef) -> tuple[MemoryReview, ...]:
        return tuple(
            self.queue.open(
                memory_id=memory.id,
                project=memory.project,
                reason=ReviewReason.SOURCE_REVISED,
                trigger_key=f"source:{source_id}:revoked",
            )
            for memory in self.store.list_memories(project, include_inactive=True)
            if any(ref.source_id == source_id for ref in memory.source_refs)
        )

    def open_due_reviews(self, *, now: datetime | None = None, project: ProjectRef | None = None) -> tuple[MemoryReview, ...]:
        moment = now or utc_now()
        projects = (project,) if project is not None else ()
        opened: list[MemoryReview] = []
        for item_project in projects:
            for memory in self.store.list_memories(item_project):
                if memory.review_due_at is not None and memory.review_due_at <= moment:
                    opened.append(self.queue.open(
                        memory_id=memory.id,
                        project=memory.project,
                        reason=ReviewReason.REVIEW_DUE,
                        trigger_key=f"due:{moment.date().isoformat()}",
                    ))
        return tuple(opened)
