from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_feedback_service import (
    RiskFeedbackService,
    parse_risk_feedback_text,
)
from project_lens.application.risk_feedback_store import (
    InMemoryRiskFeedbackStore,
    SQLiteRiskFeedbackStore,
)
from project_lens.application.risk_store import InMemoryRiskStore
from project_lens.domain.models import ProjectRef
from project_lens.domain.risk import (
    RiskFinding,
    RiskSeverity,
    RiskState,
    RiskType,
    evidence_signature,
    stable_risk_id,
)
from project_lens.domain.risk_feedback import RiskFeedbackAction
from project_lens.persistence.sqlite import SQLiteDatabase


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
PROJECT = ProjectRef(tenant_id="tenant", project_id="project")


def build_service(*, authorizer=None, feedback_store=None):
    risk_store = InMemoryRiskStore()
    engine = RiskEngine(risk_store)
    risk_id = stable_risk_id(PROJECT, RiskType.CHAT_BLOCKER, "TASK-1", ("owner-1",))
    risk_store.save(
        RiskFinding(
            risk_id=risk_id,
            project=PROJECT,
            risk_type=RiskType.CHAT_BLOCKER,
            severity=RiskSeverity.HIGH,
            title="TASK-1 当前阻塞",
            summary="群聊已确认当前阻塞。",
            primary_ref="TASK-1",
            owner_ids=("owner-1",),
            affected_refs=("TASK-1",),
            evidence_ids=(uuid4(),),
            evidence_signature=evidence_signature([]),
            detected_at=NOW,
            last_seen_at=NOW,
            state=RiskState.OPEN,
        )
    )
    store = feedback_store or InMemoryRiskFeedbackStore()
    return RiskFeedbackService(risk_engine=engine, store=store, authorizer=authorizer), engine, store, risk_id


@pytest.mark.parametrize(
    ("action", "expected_state"),
    [
        (RiskFeedbackAction.ACKNOWLEDGE, RiskState.ACKNOWLEDGED),
        (RiskFeedbackAction.DISMISS, RiskState.DISMISSED),
        (RiskFeedbackAction.UPDATE_PROGRESS, RiskState.ACKNOWLEDGED),
        (RiskFeedbackAction.REQUEST_HELP, RiskState.ACKNOWLEDGED),
    ],
)
def test_feedback_actions_are_audited_and_change_state(action, expected_state) -> None:
    service, engine, store, risk_id = build_service()

    result = service.submit(
        risk_id=risk_id,
        actor_id="owner-1",
        action=action,
        idempotency_key=f"event:{action.value}",
        reason="处理说明",
        created_at=NOW,
    )

    assert result.duplicate is False
    assert engine.get(risk_id).state == expected_state  # type: ignore[union-attr]
    assert store.list_feedback(PROJECT, risk_id=risk_id)[0].reason == "处理说明"


def test_duplicate_button_does_not_create_duplicate_feedback() -> None:
    service, _engine, store, risk_id = build_service()
    first = service.submit(
        risk_id=risk_id,
        actor_id="owner-1",
        action="acknowledge",
        idempotency_key="same-button",
        created_at=NOW,
    )
    second = service.submit(
        risk_id=risk_id,
        actor_id="owner-1",
        action="acknowledge",
        idempotency_key="same-button",
        created_at=NOW,
    )
    assert first.duplicate is False
    assert second.duplicate is True
    assert len(store.list_feedback(PROJECT, risk_id=risk_id)) == 1


def test_dismiss_reason_is_persisted() -> None:
    service, _engine, store, risk_id = build_service()
    service.submit(
        risk_id=risk_id,
        actor_id="owner-1",
        action="误报",
        idempotency_key="dismiss-1",
        reason="依赖已经由供应商解除",
        created_at=NOW,
    )
    assert store.list_feedback(PROJECT)[0].reason == "依赖已经由供应商解除"


def test_snooze_requires_future_time_and_records_it() -> None:
    service, engine, store, risk_id = build_service()
    until = NOW + timedelta(days=1)
    service.submit(
        risk_id=risk_id,
        actor_id="owner-1",
        action="snooze",
        idempotency_key="snooze-1",
        snoozed_until=until,
        created_at=NOW,
    )
    assert engine.get(risk_id).state == RiskState.SNOOZED  # type: ignore[union-attr]
    assert store.list_feedback(PROJECT)[0].snoozed_until == until


class _DenyAuthorizer:
    def can_update(self, *, actor_id, risk) -> bool:
        del actor_id, risk
        return False


def test_non_owner_feedback_is_denied_before_audit_write() -> None:
    service, _engine, store, risk_id = build_service(authorizer=_DenyAuthorizer())
    with pytest.raises(PermissionError):
        service.submit(
            risk_id=risk_id,
            actor_id="stranger",
            action="acknowledge",
            idempotency_key="denied",
            created_at=NOW,
        )
    assert store.list_feedback(PROJECT) == ()


def test_natural_language_feedback_normalizes_to_same_actions() -> None:
    service, _engine, _store, risk_id = build_service()
    parsed = parse_risk_feedback_text(f"风险 {risk_id} 误报：供应商已经恢复")
    assert parsed is not None
    assert parsed[0] == risk_id
    assert parsed[1] == RiskFeedbackAction.DISMISS
    assert "供应商已经恢复" in parsed[2]


def test_sqlite_feedback_idempotency_survives_new_adapter() -> None:
    database = SQLiteDatabase(":memory:")
    sqlite_store = SQLiteRiskFeedbackStore(database)
    service, _engine, _store, risk_id = build_service(feedback_store=sqlite_store)
    service.submit(
        risk_id=risk_id,
        actor_id="owner-1",
        action="acknowledge",
        idempotency_key="persistent-key",
        created_at=NOW,
    )
    restored = SQLiteRiskFeedbackStore(database)
    assert restored.get_feedback_by_key("persistent-key") is not None
