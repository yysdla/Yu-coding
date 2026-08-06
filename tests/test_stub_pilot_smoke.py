"""V0.1 local Stub pilot smoke: Feishu path, eight question classes, no Apply/LLM live."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi.testclient import TestClient

from project_lens.config import settings
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.main import create_app

LOCAL_TOKEN = "project-lens-local-token"


def _configure_stub_pilot(app) -> None:
    verifier = app.state.feishu_event_service._verifier
    verifier._verification_token = LOCAL_TOKEN
    verifier._signing_secret = None
    default_project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    app.state.feishu_event_service._identity_mapper = parse_project_bindings(
        "",
        default_project=default_project,
    )
    app.state.feishu_messenger = RecordingFeishuMessenger()
    app.state.feishu_event_service._messenger = app.state.feishu_messenger


def _message_payload(*, event_id: str, text: str) -> dict[str, object]:
    return {
        "schema": "2.0",
        "token": LOCAL_TOKEN,
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "tenant_key": "demo",
        },
        "event": {
            "sender": {"sender_id": {"user_id": "feishu-user-1"}},
            "message": {
                "message_id": f"message-{event_id}",
                "chat_id": "chat-1",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
        },
    }


def _ask(client: TestClient, *, event_id: str, text: str) -> UUID:
    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id=event_id, text=text),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    return UUID(body["run_id"])


def _last_card_text(app) -> str:
    interactive = [
        message
        for message in app.state.feishu_messenger.messages
        if message.message_type == "interactive"
    ]
    assert interactive, "expected at least one Feishu interactive card"
    return "\n".join(
        element.get("content", "") for element in interactive[-1].content["elements"]
    )


def _assert_readonly_answer(run) -> None:
    assert run is not None
    assert run.status == "completed"
    assert run.answer is not None
    answer = run.answer
    assert answer.claims, "pilot answers must include verified claims"
    for claim in answer.claims:
        assert claim.evidence_ids, f"claim missing evidence: {claim.text}"
    assert answer.business_summary
    assert answer.technical_summary
    for action in answer.recommended_actions:
        if action.tool_name == "engineering_proposal":
            assert action.requires_approval is True
            assert action.arguments.get("can_apply") is False
            assert action.arguments.get("allow_apply") is False


def test_stub_pilot_smoke_covers_eight_question_classes(monkeypatch) -> None:
    """End-to-end V0.1 Stub smoke for the launch-plan pilot question set."""

    monkeypatch.setattr(settings, "model_provider", "stub")
    monkeypatch.setattr(settings, "model_live", False)
    monkeypatch.setattr(settings, "model_fallback_to_stub", True)
    app = create_app()
    _configure_stub_pilot(app)
    client = TestClient(app)

    # 1) Project intro
    intro_id = _ask(client, event_id="smoke-intro", text="介绍一下这个项目")
    intro_run = app.state.run_service.get(intro_id)
    _assert_readonly_answer(intro_run)
    assert intro_run.answer.skill == "project_knowledge"
    intro_card = _last_card_text(app)
    assert "**一句话结论**" in intro_card
    assert "**来源摘要**" in intro_card
    assert "不执行 Apply" in intro_card or "不决定事实" in intro_card or "团队协作视图" in intro_card
    assert any(claim.text.startswith("项目定位：") for claim in intro_run.answer.claims)

    # 2) Project map
    map_id = _ask(client, event_id="smoke-map", text="项目地图")
    map_run = app.state.run_service.get(map_id)
    _assert_readonly_answer(map_run)
    assert map_run.answer.skill == "architecture"
    map_card = _last_card_text(app)
    assert "**一句话结论**" in map_card
    assert "核心服务" in map_card
    assert any(claim.text.startswith("核心服务：") for claim in map_run.answer.claims)

    # 3) Recent changes
    changes_id = _ask(client, event_id="smoke-changes", text="最近变更")
    changes_run = app.state.run_service.get(changes_id)
    _assert_readonly_answer(changes_run)
    assert changes_run.answer.skill == "version_change"
    assert "**一句话结论**" in _last_card_text(app)

    # 4) Knowledge gaps
    gaps_id = _ask(client, event_id="smoke-gaps", text="知识库缺什么")
    gaps_run = app.state.run_service.get(gaps_id)
    _assert_readonly_answer(gaps_run)
    assert gaps_run.answer.skill == "project_knowledge"
    gaps_card = _last_card_text(app)
    assert "**当前未知**" in gaps_card or "缺口" in gaps_card or "缺" in gaps_card

    # 5) Owners
    owner_id = _ask(client, event_id="smoke-owner", text="负责人是谁")
    owner_run = app.state.run_service.get(owner_id)
    _assert_readonly_answer(owner_run)
    owner_card = _last_card_text(app)
    assert "Ada" in owner_card or "ou_ada" in owner_card or any(
        "Ada" in claim.text or "ou_ada" in claim.text for claim in owner_run.answer.claims
    )

    # 6) Recent incidents
    incident_id = _ask(client, event_id="smoke-incident", text="最近故障")
    incident_run = app.state.run_service.get(incident_id)
    _assert_readonly_answer(incident_run)
    assert incident_run.answer.skill == "incident_diagnosis"
    incident_card = _last_card_text(app)
    assert "**一句话结论**" in incident_card
    assert "团队协作视图" in incident_card or "**来源摘要**" in incident_card

    # 7–8) RoleView switch: same ProjectAnswer, no new fact run.
    product_id = _ask(client, event_id="smoke-product", text="给产品看的版本")
    assert product_id == incident_id
    product_run = app.state.run_service.get(product_id)
    assert product_run is not None and product_run.status == "completed"
    assert product_run.answer is not None
    product_card = _last_card_text(app)
    assert "业务/产品视图" in product_card or "**一句话结论**" in product_card
    assert "立即应用" not in product_card

    tech_id = _ask(client, event_id="smoke-tech", text="展开技术细节")
    assert tech_id == incident_id
    tech_run = app.state.run_service.get(tech_id)
    assert tech_run is not None and tech_run.status == "completed"
    assert tech_run.answer is not None
    tech_card = _last_card_text(app)
    assert "技术视图" in tech_card or "相关文件" in tech_card or "**已确认事实**" in tech_card

    # Session continuity for the pilot chat
    session = app.state.conversation_service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="feishu-user-1",
        project=ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        ),
    )
    assert session.last_run_id == tech_run.id
    assert len(session.recent_turns) >= 2

    # Stub / safety: audit must not enable Apply; provider stays non-live
    assert "allow_apply: False" in tech_card or "allow_apply=False" in tech_card
    assert "立即应用" not in tech_card
    assert "PROJECT_LENS_MODEL_OPENAI_API_KEY" not in tech_card
    assert settings.model_provider == "stub"
    assert settings.model_live is False
