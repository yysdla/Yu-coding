"""Fact-type authority resolution and lightweight source gap detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from project_lens.context.source_records import FactType, SourceRecord, SourceType


DEFAULT_AUTHORITY: dict[FactType, tuple[SourceType, ...]] = {
    FactType.REQUIREMENT_SCOPE: (SourceType.FEISHU_DOCUMENT, SourceType.FEISHU_BITABLE, SourceType.MEETING_MINUTE),
    FactType.REQUIREMENT_STATUS: (SourceType.FEISHU_PROJECT, SourceType.FEISHU_BITABLE, SourceType.MEETING_MINUTE),
    FactType.DEVELOPMENT_PROGRESS: (SourceType.GITHUB_ISSUE, SourceType.GITHUB_PULL_REQUEST, SourceType.GITHUB_COMMIT, SourceType.MEETING_MINUTE),
    FactType.TEST_STATUS: (SourceType.FEISHU_DOCUMENT, SourceType.FEISHU_PROJECT, SourceType.MEETING_MINUTE),
    FactType.TECHNICAL_DECISION: (SourceType.FEISHU_DOCUMENT, SourceType.MEETING_MINUTE),
    FactType.OWNER: (SourceType.FEISHU_PROJECT, SourceType.FEISHU_BITABLE, SourceType.FEISHU_DOCUMENT, SourceType.MEETING_MINUTE),
}


@dataclass(frozen=True)
class AuthorityResolution:
    fact_type: FactType
    selected: SourceRecord | None
    candidates: tuple[SourceRecord, ...] = ()
    conflicts: tuple[SourceRecord, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class SourceGap:
    status: str
    fact_type: FactType
    project_id: str
    reason: str
    evidence_ids: tuple[str, ...] = ()
    suggested_source_type: SourceType | None = None


def resolve_fact(
    records: Iterable[SourceRecord],
    fact_type: FactType,
    *,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(days=30),
) -> AuthorityResolution:
    current = now or datetime.now(timezone.utc)
    candidates = tuple(
        item for item in records if fact_type.value in item.authority_scope or fact_type.value in item.fact_values
    )
    if not candidates:
        return AuthorityResolution(fact_type, None, reason="missing authorized source")
    ranked = sorted(candidates, key=lambda item: _score(item, fact_type, current, stale_after), reverse=True)
    selected = ranked[0]
    same_value = [item for item in ranked if _value(item, fact_type) == _value(selected, fact_type)]
    conflicts = tuple(item for item in ranked[1:] if _value(item, fact_type) != _value(selected, fact_type))
    reason = "highest authority, status, revision, and freshness score"
    if selected.status in {"proposed", "draft", "unconfirmed"}:
        reason = "best available source is not formally confirmed"
    elif conflicts:
        reason = "selected highest-scoring source; conflicting candidates retained"
    return AuthorityResolution(fact_type, selected, tuple(ranked), conflicts, reason)


def detect_gaps(
    records: Iterable[SourceRecord],
    *,
    project_id: str,
    now: datetime | None = None,
    stale_after: timedelta = timedelta(days=30),
) -> tuple[SourceGap, ...]:
    project_records = tuple(item for item in records if item.project_id == project_id and not item.revoked)
    gaps: list[SourceGap] = []
    for fact_type in FactType:
        resolution = resolve_fact(project_records, fact_type, now=now, stale_after=stale_after)
        if resolution.selected is None:
            preferred = DEFAULT_AUTHORITY[fact_type][0]
            gaps.append(SourceGap("missing", fact_type, project_id, "没有授权资料", suggested_source_type=preferred))
            continue
        if resolution.conflicts:
            gaps.append(
                SourceGap(
                    "conflict",
                    fact_type,
                    project_id,
                    "存在不同版本或不同来源的互相矛盾资料",
                    evidence_ids=tuple(item.source_id for item in resolution.candidates),
                )
            )
        if _is_stale(resolution.selected, now or datetime.now(timezone.utc), stale_after):
            gaps.append(SourceGap("stale", fact_type, project_id, "当前候选资料已超过 freshness 窗口", evidence_ids=(resolution.selected.source_id,)))
        if resolution.selected.status in {"proposed", "draft", "unconfirmed"}:
            gaps.append(SourceGap("unconfirmed", fact_type, project_id, "当前候选只有提议或草稿状态", evidence_ids=(resolution.selected.source_id,)))
    return tuple(gaps)


def _score(item: SourceRecord, fact_type: FactType, now: datetime, stale_after: timedelta) -> tuple[int, int, int, float]:
    preferred = DEFAULT_AUTHORITY[fact_type]
    authority = len(preferred) - preferred.index(item.source_type) if item.source_type in preferred else 0
    confirmed = 2 if item.status in {"confirmed", "published", "done", "closed"} else 0
    effective = 1 if item.effective_from is not None else 0
    freshness = max(0.0, 1.0 - (now - _aware(item.observed_at)).total_seconds() / max(stale_after.total_seconds(), 1.0))
    return (authority, confirmed, effective, freshness)


def _value(item: SourceRecord, fact_type: FactType) -> str:
    return item.fact_values.get(fact_type.value) or item.content_hash


def _is_stale(item: SourceRecord, now: datetime, stale_after: timedelta) -> bool:
    return _aware(now) - _aware(item.observed_at) > stale_after


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = ["AuthorityResolution", "DEFAULT_AUTHORITY", "SourceGap", "detect_gaps", "resolve_fact"]
