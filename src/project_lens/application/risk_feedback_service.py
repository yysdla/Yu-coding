"""Normalize and apply auditable owner feedback to risk state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Protocol
from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_feedback_store import RiskFeedbackStore
from project_lens.domain.risk import RiskState, ensure_utc
from project_lens.domain.risk_feedback import (
    RiskFeedback,
    RiskFeedbackAction,
    RiskFeedbackResult,
)
from project_lens.project_space.models import ProjectSpace
from project_lens.project_space.policies import RoleKind


_ACTION_ALIASES: dict[str, RiskFeedbackAction] = {
    "acknowledge": RiskFeedbackAction.ACKNOWLEDGE,
    "ack": RiskFeedbackAction.ACKNOWLEDGE,
    "确认": RiskFeedbackAction.ACKNOWLEDGE,
    "确认风险": RiskFeedbackAction.ACKNOWLEDGE,
    "知道了": RiskFeedbackAction.ACKNOWLEDGE,
    "dismiss": RiskFeedbackAction.DISMISS,
    "false_positive": RiskFeedbackAction.DISMISS,
    "误报": RiskFeedbackAction.DISMISS,
    "不是风险": RiskFeedbackAction.DISMISS,
    "snooze": RiskFeedbackAction.SNOOZE,
    "稍后提醒": RiskFeedbackAction.SNOOZE,
    "明天提醒": RiskFeedbackAction.SNOOZE,
    "update_progress": RiskFeedbackAction.UPDATE_PROGRESS,
    "更新进展": RiskFeedbackAction.UPDATE_PROGRESS,
    "已有进展": RiskFeedbackAction.UPDATE_PROGRESS,
    "request_help": RiskFeedbackAction.REQUEST_HELP,
    "请求协助": RiskFeedbackAction.REQUEST_HELP,
    "需要协助": RiskFeedbackAction.REQUEST_HELP,
}

_RISK_TEXT_PATTERN = re.compile(r"风险\s*([0-9a-f]{64})\s*[:：]?\s*(.+)", re.IGNORECASE)


class ProjectSpaceLookup(Protocol):
    def require(self, tenant_id: str, project_id: str) -> ProjectSpace: ...


class RiskFeedbackAuthorizer(Protocol):
    def can_update(self, *, actor_id: str, risk) -> bool: ...


class ProjectSpaceRiskFeedbackAuthorizer:
    def __init__(self, registry: ProjectSpaceLookup) -> None:
        self._registry = registry

    def can_update(self, *, actor_id: str, risk) -> bool:
        if actor_id in risk.owner_ids:
            return True
        space = self._registry.require(risk.project.tenant_id, risk.project.project_id)
        policy = space.role_policy_for(actor_id)
        return policy is not None and policy.role == RoleKind.MANAGER


class RiskFeedbackService:
    def __init__(
        self,
        *,
        risk_engine: RiskEngine,
        store: RiskFeedbackStore,
        authorizer: RiskFeedbackAuthorizer | None = None,
    ) -> None:
        self._risk_engine = risk_engine
        self._store = store
        self._authorizer = authorizer

    def submit(
        self,
        *,
        risk_id: str,
        actor_id: str,
        action: RiskFeedbackAction | str,
        idempotency_key: str,
        reason: str = "",
        snoozed_until: datetime | None = None,
        created_at: datetime | None = None,
    ) -> RiskFeedbackResult:
        normalized = normalize_feedback_action(action)
        now = ensure_utc(created_at or datetime.now(timezone.utc))
        if normalized == RiskFeedbackAction.SNOOZE and snoozed_until is None:
            snoozed_until = now + timedelta(days=1)
        if snoozed_until is not None:
            snoozed_until = ensure_utc(snoozed_until)
            if snoozed_until <= now:
                raise ValueError("snoozed_until must be in the future")

        finding = self._risk_engine.get(risk_id)
        if finding is None:
            raise KeyError(f"risk not found: {risk_id}")
        if self._authorizer is not None and not self._authorizer.can_update(
            actor_id=actor_id,
            risk=finding,
        ):
            raise PermissionError("actor is not allowed to update this risk")
        feedback = RiskFeedback(
            idempotency_key=idempotency_key,
            risk_id=risk_id,
            project=finding.project,
            actor_id=actor_id,
            action=normalized,
            reason=reason.strip(),
            snoozed_until=snoozed_until,
            created_at=now,
            evidence_refs=finding.evidence_ids,
        )
        stored, created = self._store.create_feedback(feedback)
        if not created:
            current = self._risk_engine.get(risk_id)
            return RiskFeedbackResult(
                feedback=stored,
                duplicate=True,
                current_state=(current.state.value if current else "unknown"),
            )

        target_state = _target_state(normalized)
        updated = self._risk_engine.transition(
            risk_id,
            target_state,
            actor_id=actor_id,
            now=now,
        )
        return RiskFeedbackResult(
            feedback=stored,
            duplicate=False,
            current_state=updated.state.value,
        )

    def due_snoozes(
        self,
        project,
        *,
        now: datetime | None = None,
    ) -> tuple[RiskFeedback, ...]:
        current = ensure_utc(now or datetime.now(timezone.utc))
        latest_by_risk: dict[str, RiskFeedback] = {}
        for feedback in self._store.list_feedback(project):
            latest_by_risk[feedback.risk_id] = feedback
        return tuple(
            feedback
            for feedback in latest_by_risk.values()
            if feedback.action == RiskFeedbackAction.SNOOZE
            and feedback.snoozed_until is not None
            and feedback.snoozed_until <= current
        )


def normalize_feedback_action(value: RiskFeedbackAction | str) -> RiskFeedbackAction:
    if isinstance(value, RiskFeedbackAction):
        return value
    normalized = value.strip().casefold().replace(" ", "_").replace("-", "_")
    action = _ACTION_ALIASES.get(normalized)
    if action is None:
        raise ValueError(f"unsupported risk feedback action: {value}")
    return action


def parse_risk_feedback_text(
    text: str,
) -> tuple[str, RiskFeedbackAction, str] | None:
    match = _RISK_TEXT_PATTERN.search(text.strip())
    if match is None:
        return None
    risk_id = match.group(1).lower()
    tail = match.group(2).strip()
    for alias in sorted(_ACTION_ALIASES, key=len, reverse=True):
        if alias in tail.casefold():
            action = _ACTION_ALIASES[alias]
            reason = tail.casefold().replace(alias, "", 1).strip(" ：:,，")
            return risk_id, action, reason
    return None


def feedback_idempotency_key(*, event_id: str, risk_id: str, actor_id: str) -> str:
    return f"feishu:{event_id}:{risk_id}:{actor_id}"


def _target_state(action: RiskFeedbackAction) -> RiskState:
    if action == RiskFeedbackAction.DISMISS:
        return RiskState.DISMISSED
    if action == RiskFeedbackAction.SNOOZE:
        return RiskState.SNOOZED
    return RiskState.ACKNOWLEDGED
