"""V0.2 Safe Live smoke: Feishu path + mocked OpenAI transport (no real network)."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi.testclient import TestClient

from project_lens.config import Settings
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.main import create_app
from project_lens.workflow.providers.factory import create_model_adapter_from_settings
from project_lens.workflow.providers.transport import RecordingChatTransport

LOCAL_TOKEN = "project-lens-local-token"
_ENHANCED_BUSINESS = "【SafeLive增强】项目定位：订单与支付协作演示。"
_ENHANCED_TECHNICAL = "【SafeLive增强】技术：create_order 为关键入口。"


def _configure_feishu(app) -> None:
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


def _install_openai_adapter(app, transport: RecordingChatTransport) -> None:
    live_settings = Settings(
        _env_file=None,
        model_provider="openai",
        model_live=True,
        model_openai_api_key="sk-test-not-for-production",
        model_openai_model="gpt-4o-mini",
        model_fallback_to_stub=True,
        model_max_retries=0,
        model_timeout_seconds=5,
    )
    adapter = create_model_adapter_from_settings(live_settings, transport=transport)
    app.state.run_service._workflow._model_adapter = adapter
    app.state.model_adapter = adapter


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
    assert interactive
    return "\n".join(
        element.get("content", "") for element in interactive[-1].content["elements"]
    )


def _ok_expression_payload() -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "business_summary": _ENHANCED_BUSINESS,
                            "technical_summary": _ENHANCED_TECHNICAL,
                            "product_summary": "产品可读摘要。",
                            "hypotheses": [],
                            "claim_drafts": [
                                {
                                    "text": "无证据伪造事实",
                                    "claim_type": "fact",
                                    "evidence_ids": ["00000000-0000-0000-0000-000000000099"],
                                }
                            ],
                            "allow_apply": False,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
    }


def test_safe_live_feishu_smoke_enhances_expression_without_apply() -> None:
    app = create_app()
    _configure_feishu(app)
    transport = RecordingChatTransport(response=_ok_expression_payload())
    _install_openai_adapter(app, transport)
    client = TestClient(app)

    run_id = _ask(client, event_id="live-smoke-intro", text="介绍一下这个项目")
    run = app.state.run_service.get(run_id)
    assert run is not None and run.status == "completed"
    assert run.answer is not None
    assert _ENHANCED_BUSINESS in run.answer.business_summary
    assert run.answer.technical_summary == _ENHANCED_TECHNICAL
    assert all(claim.evidence_ids for claim in run.answer.claims)
    assert not any(claim.text == "无证据伪造事实" for claim in run.answer.claims)
    assert any("无证据伪造事实" in item for item in run.answer.unknowns)

    adapter_result = app.state.run_service._workflow.last_model_adapter_result
    assert adapter_result is not None
    assert adapter_result.live_effective is True
    assert adapter_result.status == "ok"
    assert adapter_result.usage.get("total_tokens") == 160
    assert adapter_result.allow_apply is False

    card = _last_card_text(app)
    assert _ENHANCED_BUSINESS in card
    assert "provider:" in card and "openai" in card
    assert "usage:" in card
    assert "allow_apply: False" in card
    assert "sk-test-not-for-production" not in card
    assert "PROJECT_LENS_MODEL_OPENAI_API_KEY" not in card
    assert "立即应用" not in card
    assert "**一句话结论**" in card
    assert transport.calls
    assert transport.calls[0]["headers"]["Authorization"] == "***"


def test_safe_live_feishu_smoke_falls_back_when_provider_fails() -> None:
    app = create_app()
    _configure_feishu(app)
    transport = RecordingChatTransport(error=RuntimeError("network down"))
    _install_openai_adapter(app, transport)
    client = TestClient(app)

    run_id = _ask(client, event_id="live-smoke-fallback", text="项目地图")
    run = app.state.run_service.get(run_id)
    assert run is not None and run.status == "completed"
    assert run.answer is not None
    assert run.answer.skill == "architecture"
    assert any(claim.evidence_ids for claim in run.answer.claims)

    adapter_result = app.state.run_service._workflow.last_model_adapter_result
    assert adapter_result is not None
    assert adapter_result.fallback_used is True
    assert adapter_result.allow_apply is False

    card = _last_card_text(app)
    assert "**一句话结论**" in card
    assert "allow_apply: False" in card
    assert "立即应用" not in card
    assert "sk-test" not in card
