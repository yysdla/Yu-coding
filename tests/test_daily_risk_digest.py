from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from project_lens.application.daily_risk_digest import (
    DailyRiskDigestService,
    render_daily_risk_digest,
)
from project_lens.application.risk_feedback_store import InMemoryRiskFeedbackStore
from project_lens.domain.models import ProjectRef
from project_lens.domain.risk import (
    RiskFinding,
    RiskSeverity,
    RiskState,
    RiskType,
    evidence_signature,
    stable_risk_id,
)
from project_lens.domain.risk_feedback import RiskFeedback, RiskFeedbackAction


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
PROJECT = ProjectRef(tenant_id="tenant", project_id="project")


def finding(ref: str, *, severity=RiskSeverity.MEDIUM, state=RiskState.OPEN, owner="owner-1"):
    risk_id = stable_risk_id(PROJECT, RiskType.OVERDUE_TASK, ref, (owner,))
    return RiskFinding(
        risk_id=risk_id,
        project=PROJECT,
        risk_type=RiskType.OVERDUE_TASK,
        severity=severity,
        title=f"任务 {ref} 风险",
        summary="PRIVATE CHAT BODY secret-token should never enter digest",
        primary_ref=ref,
        owner_ids=(owner,),
        affected_refs=(ref,),
        evidence_ids=(uuid4(),),
        evidence_signature=evidence_signature([]),
        detected_at=NOW,
        last_seen_at=NOW,
        state=state,
        resolved_at=NOW if state == RiskState.RESOLVED else None,
        due_at=NOW + timedelta(hours=4),
    )


def test_daily_digest_groups_by_owner_and_expected_categories() -> None:
    store = InMemoryRiskFeedbackStore()
    open_risk = finding("TASK-1")
    acknowledged = finding("TASK-2", state=RiskState.ACKNOWLEDGED)
    high = finding("TASK-3", severity=RiskSeverity.HIGH, owner="owner-2")

    digest = DailyRiskDigestService(store).build(
        PROJECT,
        (open_risk, acknowledged, high),
        now=NOW,
    )

    assert {section.owner_id for section in digest.owner_sections} == {"owner-1", "owner-2"}
    owner_one = next(item for item in digest.owner_sections if item.owner_id == "owner-1")
    assert {item.risk_id for item in owner_one.unacknowledged} == {open_risk.risk_id}
    assert {item.risk_id for item in owner_one.acknowledged_open} == {acknowledged.risk_id}
    assert len(owner_one.due_today) == 2


def test_digest_counts_resolved_and_false_positive_feedback() -> None:
    store = InMemoryRiskFeedbackStore()
    resolved = finding("TASK-1", state=RiskState.RESOLVED)
    dismissed = finding("TASK-2", state=RiskState.DISMISSED)
    store.create_feedback(
        RiskFeedback(
            idempotency_key="dismiss",
            risk_id=dismissed.risk_id,
            project=PROJECT,
            actor_id="owner-1",
            action=RiskFeedbackAction.DISMISS,
            reason="误报",
            created_at=NOW,
            evidence_refs=dismissed.evidence_ids,
        )
    )
    digest = DailyRiskDigestService(store).build(PROJECT, (resolved, dismissed), now=NOW)
    assert digest.resolved_count == 1
    assert digest.dismissed_count == 1


def test_digest_card_never_contains_risk_summary_or_evidence_body() -> None:
    risk = finding("TASK-1")
    digest = DailyRiskDigestService(InMemoryRiskFeedbackStore()).build(
        PROJECT,
        (risk,),
        now=NOW,
    )
    card_text = str(render_daily_risk_digest(digest))
    assert "PRIVATE CHAT BODY" not in card_text
    assert "secret-token" not in card_text
    assert str(risk.evidence_ids[0]) not in card_text
    assert "TASK-1" in card_text
