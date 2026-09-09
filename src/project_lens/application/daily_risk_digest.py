"""Safe daily risk digest aggregation by project and responsible owner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from project_lens.application.risk_feedback_store import RiskFeedbackStore
from project_lens.domain.models import ProjectRef
from project_lens.domain.risk import RiskFinding, RiskSeverity, RiskState, ensure_utc
from project_lens.domain.risk_feedback import RiskFeedbackAction


@dataclass(frozen=True)
class RiskDigestItem:
    risk_id: str
    title: str
    severity: RiskSeverity
    state: RiskState
    affected_refs: tuple[str, ...]
    owner_ids: tuple[str, ...]
    detected_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class OwnerRiskDigest:
    owner_id: str
    new_high: tuple[RiskDigestItem, ...]
    unacknowledged: tuple[RiskDigestItem, ...]
    acknowledged_open: tuple[RiskDigestItem, ...]
    due_today: tuple[RiskDigestItem, ...]


@dataclass(frozen=True)
class DailyRiskDigest:
    project: ProjectRef
    digest_date: date
    owner_sections: tuple[OwnerRiskDigest, ...]
    resolved_count: int
    dismissed_count: int


class DailyRiskDigestService:
    def __init__(self, feedback_store: RiskFeedbackStore) -> None:
        self._feedback_store = feedback_store

    def build(
        self,
        project: ProjectRef,
        findings: tuple[RiskFinding, ...],
        *,
        now: datetime | None = None,
    ) -> DailyRiskDigest:
        current = ensure_utc(now or datetime.now(timezone.utc))
        today = current.date()
        feedback = self._feedback_store.list_feedback(project)
        dismissed_ids = {
            item.risk_id
            for item in feedback
            if item.action == RiskFeedbackAction.DISMISS
            and ensure_utc(item.created_at).date() == today
        }
        resolved_count = sum(
            finding.state == RiskState.RESOLVED and finding.resolved_at is not None
            and ensure_utc(finding.resolved_at).date() == today
            for finding in findings
        )
        dismissed_count = len(dismissed_ids)
        owners = sorted(
            {
                owner
                for finding in findings
                if finding.state not in {RiskState.RESOLVED, RiskState.DISMISSED}
                for owner in (finding.owner_ids or ("project_owners",))
            }
        )
        sections: list[OwnerRiskDigest] = []
        for owner in owners:
            relevant = tuple(
                finding
                for finding in findings
                if owner in (finding.owner_ids or ("project_owners",))
            )
            sections.append(
                OwnerRiskDigest(
                    owner_id=owner,
                    new_high=tuple(
                        _item(finding)
                        for finding in relevant
                        if finding.severity == RiskSeverity.HIGH
                        and ensure_utc(finding.detected_at).date() == today
                    ),
                    unacknowledged=tuple(
                        _item(finding)
                        for finding in relevant
                        if finding.state == RiskState.OPEN
                    ),
                    acknowledged_open=tuple(
                        _item(finding)
                        for finding in relevant
                        if finding.state in {RiskState.ACKNOWLEDGED, RiskState.SNOOZED}
                    ),
                    due_today=tuple(
                        _item(finding)
                        for finding in relevant
                        if _due_today(finding, today)
                    ),
                )
            )
        return DailyRiskDigest(
            project=project,
            digest_date=today,
            owner_sections=tuple(sections),
            resolved_count=resolved_count,
            dismissed_count=dismissed_count,
        )


def render_daily_risk_digest(digest: DailyRiskDigest) -> dict[str, object]:
    elements: list[dict[str, object]] = [
        {
            "tag": "markdown",
            "content": (
                f"**项目**\n{digest.project.project_id}\n"
                f"**日期**\n{digest.digest_date.isoformat()}\n"
                f"**今日处理**\n已解决 {digest.resolved_count}，误报 {digest.dismissed_count}"
            ),
        }
    ]
    for section in digest.owner_sections:
        lines = [f"**负责人：{section.owner_id}**"]
        _append_items(lines, "新增高风险", section.new_high)
        _append_items(lines, "未确认风险", section.unacknowledged)
        _append_items(lines, "已确认未解决", section.acknowledged_open)
        _append_items(lines, "今日到期", section.due_today)
        elements.append({"tag": "markdown", "content": "\n".join(lines)})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "ProjectLens 每日风险摘要"},
        },
        "elements": elements,
    }


def _item(finding: RiskFinding) -> RiskDigestItem:
    return RiskDigestItem(
        risk_id=finding.risk_id,
        title=finding.title,
        severity=finding.severity,
        state=finding.state,
        affected_refs=finding.affected_refs,
        owner_ids=finding.owner_ids,
        detected_at=finding.detected_at,
        last_seen_at=finding.last_seen_at,
    )


def _due_today(finding: RiskFinding, today: date) -> bool:
    return finding.due_at is not None and ensure_utc(finding.due_at).date() == today


def _append_items(lines: list[str], label: str, items: tuple[RiskDigestItem, ...]) -> None:
    if not items:
        return
    lines.append(f"{label}（{len(items)}）")
    for item in items[:10]:
        refs = ", ".join(item.affected_refs[:3]) or "项目范围"
        lines.append(f"- [{item.severity.value}] {item.title}；影响：{refs}")
