from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from project_lens.domain.models import ProjectRef
from project_lens.domain.risk import (
    RiskFinding,
    RiskSeverity,
    RiskState,
    RiskType,
    evidence_signature,
    stable_risk_id,
)
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.cards import render_risk_card
from project_lens.main import create_app


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
PROJECT = ProjectRef(
    tenant_id="demo",
    project_id="payment",
    service="order-service",
    environment="production",
)
LOCAL_TOKEN = "project-lens-local-token"


def finding() -> RiskFinding:
    risk_id = stable_risk_id(PROJECT, RiskType.CHAT_BLOCKER, "TASK-1", ("u1",))
    return RiskFinding(
        risk_id=risk_id,
        project=PROJECT,
        risk_type=RiskType.CHAT_BLOCKER,
        severity=RiskSeverity.HIGH,
        title="TASK-1 当前阻塞",
        summary="接口权限未开通，任务无法继续。",
        primary_ref="TASK-1",
        owner_ids=("u1",),
        affected_refs=("TASK-1", "API-GATEWAY"),
        evidence_ids=(uuid4(), uuid4()),
        evidence_signature=evidence_signature([]),
        detected_at=NOW,
        last_seen_at=NOW,
        state=RiskState.OPEN,
    )


def test_risk_card_contains_required_fields_and_actions() -> None:
    risk = finding()
    card = render_risk_card(risk)
    text = str(card)
    assert risk.title in text
    assert "风险等级" in text
    assert "影响对象" in text
    assert "关键证据引用" in text
    assert "检测时间" in text
    assert "数据新鲜度" in text
    for action in (
        "risk_acknowledge",
        "risk_dismiss",
        "risk_snooze",
        "risk_update_progress",
        "risk_request_help",
    ):
        assert action in text
    assert "为什么判断为风险" in text


def test_group_safe_card_has_no_feedback_buttons_or_private_body() -> None:
    card = render_risk_card(finding(), group_safe=True)
    text = str(card)
    assert "群内不展示来源正文" in text
    assert "risk_acknowledge" not in text


def _card_payload(*, event_id: str, risk_id: str) -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "card.action.trigger",
            "tenant_key": "demo",
        },
        "event": {
            "operator": {"open_id": "u1", "user_id": "u1"},
            "action": {
                "tag": "button",
                "value": {"action": "risk_acknowledge", "risk_id": risk_id},
            },
            "context": {"open_chat_id": "oc_payment"},
        },
        "token": LOCAL_TOKEN,
    }


def test_duplicate_feishu_button_does_not_create_duplicate_feedback() -> None:
    app = create_app()
    verifier = app.state.feishu_event_service._verifier
    verifier._verification_token = LOCAL_TOKEN
    verifier._signing_secret = None
    app.state.feishu_messenger = RecordingFeishuMessenger()
    app.state.feishu_event_service._messenger = app.state.feishu_messenger
    risk = finding()
    app.state.risk_store.save(risk)
    client = TestClient(app)

    first = client.post(
        "/api/v1/feishu/events",
        json=_card_payload(event_id="risk-button-1", risk_id=risk.risk_id),
    )
    duplicate = client.post(
        "/api/v1/feishu/events",
        json=_card_payload(event_id="risk-button-1", risk_id=risk.risk_id),
    )

    assert first.status_code == 200
    assert first.json()["status"] == "accepted"
    assert duplicate.json()["status"] == "duplicate"
    feedback = app.state.risk_feedback_store.list_feedback(PROJECT, risk_id=risk.risk_id)
    assert len(feedback) == 1
    assert app.state.risk_engine.get(risk.risk_id).state == RiskState.ACKNOWLEDGED
    assert len(app.state.feishu_messenger.messages) == 1
