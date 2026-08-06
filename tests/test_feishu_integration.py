import json
from uuid import UUID

from fastapi.testclient import TestClient

from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.security import build_signature
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.main import create_app


TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""

LOCAL_TOKEN = "project-lens-local-token"


def _configure_verifier(app, *, signing_secret: str | None = None) -> None:
    verifier = app.state.feishu_event_service._verifier
    verifier._verification_token = LOCAL_TOKEN
    verifier._signing_secret = signing_secret
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


def test_feishu_url_verification_challenge() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)
    response = client.post(
        "/api/v1/feishu/events",
        json={
            "type": "url_verification",
            "token": LOCAL_TOKEN,
            "challenge": "challenge-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-token"}


def test_feishu_url_verification_without_signature_when_encrypt_key_configured() -> None:
    app = create_app()
    _configure_verifier(app, signing_secret="secret")
    client = TestClient(app)
    response = client.post(
        "/api/v1/feishu/events",
        json={
            "type": "url_verification",
            "token": LOCAL_TOKEN,
            "challenge": "challenge-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-token"}


def test_feishu_signed_url_verification_challenge() -> None:
    app = create_app()
    _configure_verifier(app, signing_secret="secret")
    client = TestClient(app)
    body = json.dumps(
        {
            "type": "url_verification",
            "token": LOCAL_TOKEN,
            "challenge": "challenge-token",
        },
        separators=(",", ":"),
    ).encode()
    signature = build_signature(
        body=body,
        timestamp="123",
        nonce="abc",
        signing_secret="secret",
    )

    response = client.post(
        "/api/v1/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": "123",
            "X-Lark-Request-Nonce": "abc",
            "X-Lark-Signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-token"}


def test_feishu_v2_event_without_token_when_signed() -> None:
    app = create_app()
    _configure_verifier(app, signing_secret="secret")
    client = TestClient(app)
    body = json.dumps(
        _message_payload(event_id="signed-v2", text="hello"),
        separators=(",", ":"),
    ).encode()
    signature = build_signature(
        body=body,
        timestamp="123",
        nonce="abc",
        signing_secret="secret",
    )

    response = client.post(
        "/api/v1/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": "123",
            "X-Lark-Request-Nonce": "abc",
            "X-Lark-Signature": signature,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_feishu_message_event_creates_run_executes_and_posts_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="event-1", text=TRACEBACK),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    messages = app.state.feishu_messenger.messages
    assert len(messages) == 2
    assert messages[0].message_type == "text"
    assert messages[0].chat_id == "chat-1"
    assert messages[1].message_type == "interactive"
    assert messages[1].content["header"]["title"]["content"] == "ProjectLens 故障协作报告"
    assert not any(
        element.get("content", "").startswith("**当前 Skill**")
        for element in messages[1].content["elements"]
    )
    card_text_early = "\n".join(
        element.get("content", "") for element in messages[1].content["elements"]
    )
    assert "**你问的是**" in card_text_early
    assert "**调试信息**" in card_text_early
    assert "内部路由：incident_diagnosis" in card_text_early or "incident_diagnosis" in card_text_early
    assert "**一句话结论**" in card_text_early
    assert any(
        "当前状态" in element.get("content", "")
        or "回答状态" in element.get("content", "")
        or "来源摘要" in element.get("content", "")
        for element in messages[1].content["elements"]
    )
    card_text = "\n".join(
        element.get("content", "") for element in messages[1].content["elements"]
    )
    assert "**已确认**" in card_text or "**一句话结论**" in card_text
    assert "**修复提案**" in card_text
    assert "问题解释:" in card_text
    assert "affected_paths:" in card_text
    assert "patch plan:" in card_text
    assert "diff 摘要" in card_text
    assert "test commands:" in card_text
    assert "test result: passed=" in card_text
    assert "requires_approval=True" in card_text
    assert "can_apply=False" in card_text
    assert "allow_apply=False" in card_text
    assert "不会执行 Apply" in card_text
    assert "立即应用" not in card_text
    assert "Apply" not in card_text or "不会执行 Apply" in card_text
    assert not any(
        element.get("tag") == "action"
        and any(
            "apply" in str(action.get("value", {})).lower()
            or "立即应用" in str(action.get("text", {}))
            for action in element.get("actions", [])
        )
        for element in messages[1].content["elements"]
    )

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run.status == "completed"
    assert run.answer is not None
    eng = next(
        action
        for action in run.answer.recommended_actions
        if action.tool_name == "engineering_proposal"
    )
    assert eng.requires_approval is True
    assert eng.arguments["can_apply"] is False


def test_feishu_version_question_posts_impact_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="version-event", text="这个版本上线后影响了什么"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    messages = app.state.feishu_messenger.messages
    assert len(messages) == 2
    card_text = "\n".join(
        element.get("content", "") for element in messages[1].content["elements"]
    )
    assert messages[1].content["header"]["title"]["content"] == "ProjectLens 变更影响报告"
    assert "**一句话结论**" in card_text
    assert "**来源摘要**" in card_text
    assert "version_change" in card_text  # debug zone internal route
    assert "**当前 Skill**" not in card_text
    assert "**可继续追问**" in card_text
    assert "需要回归哪些入口和场景？" in card_text
    assert "影响" in card_text

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run.status == "completed"
    assert run.answer.skill == "version_change"


def test_feishu_project_intro_shortcut_posts_intro_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="project-intro-event", text="介绍一下这个项目"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    card_text = _last_card_text(app)
    assert "**当前 Skill**" not in card_text
    assert "ProjectLens 项目概览" in str(app.state.feishu_messenger.messages[-1].content)
    assert "**一句话结论**" in card_text
    assert "**来源摘要**" in card_text
    assert "不执行 Apply" in card_text or "不决定事实" in card_text or "团队协作视图" in card_text
    assert "**调试信息**" in card_text
    assert "project_knowledge" in card_text  # only inside debug zone

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run.status == "completed"
    assert "介绍一下这个项目" in run.question
    assert run.answer.skill == "project_knowledge"
    claim_texts = [claim.text for claim in run.answer.claims]
    assert any(text.startswith("项目定位：") for text in claim_texts)
    assert any(text.startswith("核心服务：") for text in claim_texts)
    assert any(text.startswith("关键入口：") for text in claim_texts)
    assert any(text.startswith("主要文档：") for text in claim_texts)
    assert any("负责人" in text for text in claim_texts)
    assert any(text.startswith("最近变更：") for text in claim_texts)
    for claim in run.answer.claims:
        assert claim.evidence_ids
    assert run.answer.confidence >= 0
    # Expression-only LLM prep: Apply stays disabled on the answer path.
    assert not any(
        "Apply" in action.title and action.requires_approval is False
        for action in run.answer.recommended_actions
    )


def test_feishu_project_map_shortcut_posts_project_map_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="project-map-event", text="项目地图"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    card_text = _last_card_text(app)
    assert "architecture" in card_text  # debug internal route
    assert "**一句话结论**" in card_text
    assert "**来源摘要**" in card_text
    assert "**当前 Skill**" not in card_text
    assert "架构理解" not in card_text or "内部路由" in card_text
    assert app.state.feishu_messenger.messages[-1].content["header"]["title"]["content"] == (
        "ProjectLens 项目地图"
    )
    assert "核心服务" in card_text
    assert "关键入口" in card_text
    assert "上下游" in card_text
    assert "团队协作视图" in card_text or "给技术看的版本" in card_text

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run.status == "completed"
    assert run.question == "请解释这个项目的架构、服务、入口、依赖和风险，形成项目地图。"
    assert run.answer.skill == "architecture"
    claim_texts = [claim.text for claim in run.answer.claims]
    assert any(text.startswith("核心服务：") for text in claim_texts)
    assert any(text.startswith("关键入口：") for text in claim_texts)
    assert any(text.startswith("上下游依赖：") for text in claim_texts)
    for claim in run.answer.claims:
        assert claim.evidence_ids


def test_feishu_recent_changes_shortcut_posts_change_impact_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="recent-change-event", text="最近变更"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    card_text = _last_card_text(app)
    assert "version_change" in card_text
    assert "**一句话结论**" in card_text
    assert "**来源摘要**" in card_text
    assert "**当前 Skill**" not in card_text
    assert app.state.feishu_messenger.messages[-1].content["header"]["title"]["content"] == (
        "ProjectLens 变更影响报告"
    )

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run.status == "completed"
    assert run.answer.skill == "version_change"
    assert run.answer is not None
    # Bootstrapped git fixture should be available to the change-impact workflow.
    assert any(item.type.value == "commit" for item in run.answer.evidence) or any(
        "null guard" in item.content.lower() or "commit" in item.content.lower()
        for item in run.answer.evidence
    ) or any("commit" in claim.text.lower() for claim in run.answer.claims)


def test_feishu_recent_incidents_shortcut_posts_incident_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="recent-incident-event", text="最近故障"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    card_text = _last_card_text(app)
    assert "incident_diagnosis" in card_text
    assert "**一句话结论**" in card_text
    assert "**当前 Skill**" not in card_text
    assert "create_order" in card_text or "/orders" in card_text
    assert app.state.feishu_messenger.messages[-1].content["header"]["title"]["content"] == (
        "ProjectLens 故障协作报告"
    )

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run.status == "completed"
    assert run.answer.skill == "incident_diagnosis"
    claim_blob = "\n".join(claim.text for claim in run.answer.claims)
    assert "ProjectOps 相关性分析" in claim_blob
    assert (
        "ProjectOps 时间线相关性" in claim_blob
        or "ProjectOps timeline correlation" in claim_blob
    )
    assert "REL-2026-07-20" in claim_blob or "REL-2026-07-20" in card_text
    assert "release_after_ops" in claim_blob or "release_after_ops" in card_text
    assert "/orders" in claim_blob or "/orders" in card_text
    assert "create_order" in claim_blob or "create_order" in card_text
    assert any("运维时间窗信号" in claim.text for claim in run.answer.claims)
    assert any(item.source.system == "ops_window" for item in run.answer.evidence)


def test_feishu_knowledge_gap_shortcut_posts_gap_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="knowledge-gap-event", text="知识库缺什么"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    card_text = _last_card_text(app)
    assert "项目知识" in card_text
    assert "project_knowledge" in card_text
    assert "**一句话结论**" in card_text or "**当前未知**" in card_text
    assert (
        "[" in card_text
        or "缺口：" in card_text
        or "未发现明确缺口" in card_text
        or "缺" in card_text
    )
    # Entity-aware Phase 6 gaps usually surface API/module coverage on the demo.
    assert "建议谁补" in card_text or "建议" in card_text or "未发现明确缺口" in card_text

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run.status == "completed"
    assert run.answer.skill == "project_knowledge"


def test_feishu_owner_lookup_shortcut_posts_owner_card() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="owner-lookup-event", text="负责人是谁"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    card_text = _last_card_text(app)
    assert "**一句话结论**" in card_text
    assert "**当前 Skill**" not in card_text
    assert "project_knowledge" in card_text  # debug zone
    assert app.state.feishu_messenger.messages[-1].content["header"]["title"]["content"] == (
        "ProjectLens 负责人视图"
    )
    assert "Ada" in card_text or "ou_ada" in card_text
    assert "项目关系图显示" in card_text or "负责人" in card_text or "Ada" in card_text

    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run is not None
    assert run.status == "completed"
    assert run.question.startswith("这个项目的负责人是谁")
    assert run.answer is not None
    assert run.answer.skill == "project_knowledge"
    assert any(
        "Ada" in claim.text or "ou_ada" in claim.text for claim in run.answer.claims
    )
    assert any(
        item.metadata.get("owner_user_id") == "ou_ada" for item in run.answer.evidence
    )
    assert any("项目关系图显示" in claim.text for claim in run.answer.claims)


def test_feishu_followup_who_changed_reuses_incident_session() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    first = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-incident-1", text=TRACEBACK),
    )
    assert first.status_code == 200
    first_run_id = UUID(first.json()["run_id"])

    second = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-incident-2", text="那是谁改的？"),
    )
    assert second.status_code == 200
    second_run = app.state.run_service.get(UUID(second.json()["run_id"]))
    assert second_run is not None
    assert "故障诊断" in second_run.question
    assert "commit" in second_run.question

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
    assert session.last_run_id == second_run.id
    assert len(session.recent_turns) >= 2
    assert session.summary.active_skill == "incident_diagnosis"
    assert session.recent_turns[-1].rewritten_question == second_run.question
    assert first_run_id != second_run.id


def test_feishu_followup_impact_after_version_question() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    client.post(
        "/api/v1/feishu/events",
        json=_message_payload(
            event_id="followup-version-1",
            text="这个项目最近有哪些版本、发布、变更、commit，以及它们可能影响了什么？",
        ),
    )
    second = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-version-2", text="影响哪里？"),
    )
    run = app.state.run_service.get(UUID(second.json()["run_id"]))
    assert run is not None
    assert "版本变更" in run.question or "故障诊断" in run.question
    assert "受影响服务" in run.question


def test_feishu_followup_how_to_fix_does_not_apply() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-fix-1", text=TRACEBACK),
    )
    second = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-fix-2", text="怎么修？"),
    )
    run = app.state.run_service.get(UUID(second.json()["run_id"]))
    assert run is not None
    assert "不执行 Apply" in run.question
    assert run.answer is not None
    eng = next(
        action
        for action in run.answer.recommended_actions
        if action.tool_name == "engineering_proposal"
    )
    assert eng.arguments.get("can_apply") is False
    interactive = [
        message
        for message in app.state.feishu_messenger.messages
        if message.message_type == "interactive"
    ]
    assert interactive
    card_text = "\n".join(
        element.get("content", "") for element in interactive[-1].content["elements"]
    )
    assert "不会执行 Apply" in card_text
    assert "立即应用" not in card_text


def test_feishu_followup_for_product_enters_business_summary() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    first = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-product-1", text=TRACEBACK),
    )
    first_run_id = first.json()["run_id"]
    second = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-product-2", text="发给产品看"),
    )
    # RoleView short-circuit reuses the same ProjectAnswer run.
    assert second.json()["run_id"] == first_run_id
    run = app.state.run_service.get(UUID(second.json()["run_id"]))
    assert run is not None
    assert run.answer is not None
    assert run.answer.business_summary
    card_text = _last_card_text(app)
    assert "业务/产品视图" in card_text or "业务" in card_text
    assert "**一句话结论**" in card_text


def test_feishu_followup_sync_status_skips_agent_run() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="followup-sync-1", text="同步了吗？"),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert "run_id" not in response.json()
    messages = app.state.feishu_messenger.messages
    assert len(messages) == 1
    assert messages[0].message_type == "interactive"
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
    assert len(session.recent_turns) == 1
    assert session.recent_turns[0].rewritten_question is not None
    assert "同步状态" in session.recent_turns[0].rewritten_question
    # Session summary must not become ProjectMemory.
    assert session.summary.active_skill is None
    assert app.state.memory_store.list_memories(
        ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        )
    ) == ()


def test_feishu_non_followup_keeps_original_question() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)
    text = "请解释 order_service.create_order 的实现"
    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="non-followup-1", text=text),
    )
    run = app.state.run_service.get(UUID(response.json()["run_id"]))
    assert run is not None
    assert run.question == text


def test_feishu_empty_message_is_ignored() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    response = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="empty-message-event", text="   "),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert app.state.feishu_messenger.messages == []


def test_feishu_shortcut_on_unmapped_chat_is_forbidden() -> None:
    app = create_app()
    _configure_verifier(app)
    app.state.feishu_event_service._identity_mapper = parse_project_bindings(
        "",
        default_project=ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        ),
    )
    # Default bindings only map chat-1; send shortcut from another chat.
    client = TestClient(app)
    payload = _message_payload(event_id="unmapped-shortcut", text="负责人是谁")
    payload["event"]["message"]["chat_id"] = "chat-unmapped"  # type: ignore[index]

    response = client.post("/api/v1/feishu/events", json=payload)

    assert response.status_code == 403
    assert "not mapped" in response.json()["detail"]
    assert app.state.feishu_messenger.messages == []


def test_feishu_event_id_is_idempotent() -> None:
    app = create_app()
    _configure_verifier(app)
    client = TestClient(app)

    first = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="same-event", text=TRACEBACK),
    )
    second = client.post(
        "/api/v1/feishu/events",
        json=_message_payload(event_id="same-event", text=TRACEBACK),
    )

    assert first.json()["status"] == "accepted"
    assert second.json() == {"status": "duplicate"}
    assert len(app.state.feishu_messenger.messages) == 2


def test_feishu_signature_rejects_tampered_body() -> None:
    app = create_app()
    _configure_verifier(app, signing_secret="secret")
    client = TestClient(app)
    body = json.dumps(
        _message_payload(event_id="signed-event", text=TRACEBACK),
        separators=(",", ":"),
    ).encode()
    signature = build_signature(
        body=b"tampered",
        timestamp="123",
        nonce="abc",
        signing_secret="secret",
    )

    response = client.post(
        "/api/v1/feishu/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Lark-Request-Timestamp": "123",
            "X-Lark-Request-Nonce": "abc",
            "X-Lark-Signature": signature,
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid Feishu request signature"


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


def _last_card_text(app) -> str:
    messages = [
        item
        for item in app.state.feishu_messenger.messages
        if item.message_type == "interactive"
    ]
    assert messages
    return "\n".join(
        element.get("content", "") for element in messages[-1].content["elements"]
    )
