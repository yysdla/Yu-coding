"""Retention policies for historical Episode and Observation records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from project_lens.context.history_store import HistoryMemoryStore
from project_lens.domain.models import ProjectRef


@dataclass(frozen=True)
class HistoryRetentionPolicy:
    """Bounded retention settings for one project history partition."""

    max_age_days: int = 180

    def cutoff(self, *, now: datetime | None = None) -> datetime:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current - timedelta(days=max(1, int(self.max_age_days)))


@dataclass(frozen=True)
class HistoryRetentionReport:
    tenant_id: str
    project_id: str
    cutoff: datetime
    removed_episode_count: int


def apply_history_retention(
    store: HistoryMemoryStore,
    project: ProjectRef,
    policy: HistoryRetentionPolicy,
    *,
    now: datetime | None = None,
) -> HistoryRetentionReport:
    """Prune only historical records in the explicitly named project."""

    cutoff = policy.cutoff(now=now)
    removed = store.prune_before(project, cutoff)
    return HistoryRetentionReport(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        cutoff=cutoff,
        removed_episode_count=removed,
    )
