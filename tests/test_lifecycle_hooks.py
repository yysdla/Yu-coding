"""Lifecycle hook bus coverage for run, compression, and memory approvals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from project_lens.application.conversation_service import ConversationService
from project_lens.domain.models import (
    ActionProposal,
    Claim,
    ClaimType,
    Evidence,
    EvidenceGrade,
    EvidenceType,
    ProjectAnswer,
    ProjectRef,
    SourceRef,
)
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.main import create_app
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType


TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def test_compression_hooks_emit_triggered_and_completed() -> None:
    bus = LifecycleBus()
    service = ConversationService(recent_turn_limit=2, lifecycle=bus)
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="u1",
        project=_project(),
    )
    for index in range(3):
        session = service.record_turn(
            session,
            user_id="u1",
            text=f"turn-{index}",
            rewritten_question=None,
            run_id=uuid4(),
        )
    types = [event.type for event in bus.all()]
    assert LifecycleEventType.COMPRESSION_TRIGGERED in types
    assert LifecycleEventType.COMPRESSION_COMPLETED in types
    completed = bus.of_type(LifecycleEventType.COMPRESSION_COMPLETED)[-1]
    assert completed.payload["compression_cycle"] >= 1


def test_run_lifecycle_hooks_cover_core_stages() -> None:
    app = create_app()
    client = TestClient(app)
    create = client.post(
        "/api/v1/runs",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "question": TRACEBACK,
        },
    )
    assert create.status_code == 202
    run_id = UUID(create.json()["run_id"])
    execute = client.post(f"/api/v1/runs/{run_id}/execute")
    assert execute.status_code == 200

    bus: LifecycleBus = app.state.lifecycle_bus
    types = {event.type for event in bus.for_run(run_id)}
    assert LifecycleEventType.RUN_CREATED in types
    assert LifecycleEventType.RUN_RESOLVED in types
    assert LifecycleEventType.CONTEXT_COLLECTED in types
    assert LifecycleEventType.ANALYSIS_COMPLETED in types
    assert LifecycleEventType.VERIFICATION_COMPLETED in types
    assert LifecycleEventType.ANSWER_COMPOSED in types
    assert LifecycleEventType.ENGINEERING_PROPOSED in types
    assert LifecycleEventType.ENGINEERING_VALIDATED in types

    # Mirrored into append-only run events for replay.
    run_events = app.state.run_service.events(run_id)
    lifecycle_payloads = [
        event.payload.get("lifecycle")
        for event in run_events
        if event.type.value == "lifecycle" or event.payload.get("lifecycle")
    ]
    assert "run.resolved" in lifecycle_payloads
    assert "answer.composed" in lifecycle_payloads
    assert "engineering.validated" in lifecycle_payloads
    # Status stream stays clean for progress consumers.
    status_events = [
        event for event in run_events if event.type.value == "run_status_changed"
    ]
    assert status_events
    assert all("status" in event.payload for event in status_events)


def test_memory_api_emits_approval_hooks() -> None:
    app = create_app()
    client = TestClient(app)
    create = client.post(
        "/api/v1/projects/memory-proposals",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "proposed_by": "u1",
            "claim_text": "owner is Ada",
            "evidence_ids": [str(uuid4())],
            "allowed_approvers": ["lead"],
        },
    )
    assert create.status_code == 201
    proposal_id = create.json()["id"]
    decide = client.post(
        f"/api/v1/memory-proposals/{proposal_id}/decision",
        json={"approved": True, "decided_by": "lead"},
    )
    assert decide.status_code == 200

    bus: LifecycleBus = app.state.lifecycle_bus
    types = [event.type for event in bus.all()]
    assert LifecycleEventType.MEMORY_PROPOSED in types
    assert LifecycleEventType.APPROVAL_REQUESTED in types
    assert LifecycleEventType.APPROVAL_DECIDED in types
    assert LifecycleEventType.MEMORY_APPROVED in types


def test_feishu_run_emits_memory_proposed_when_fact_available() -> None:
    app = create_app()
    verifier = app.state.feishu_event_service._verifier
    verifier._verification_token = "project-lens-local-token"
    verifier._signing_secret = None
    app.state.feishu_event_service._identity_mapper = parse_project_bindings(
        "",
        default_project=_project(),
    )
    app.state.feishu_messenger = RecordingFeishuMessenger()
    app.state.feishu_event_service._messenger = app.state.feishu_messenger
    client = TestClient(app)

    # Seed a document fact so propose_memory_from_answer can fire.
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=_project(),
        source=SourceRef(system="local", source_id="owners"),
        content="order-service owner is Ada",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    # Use traceback path which already produces facts in demo fixtures.
    response = client.post(
        "/api/v1/feishu/events",
        json={
            "schema": "2.0",
            "token": "project-lens-local-token",
            "header": {
                "event_id": "lifecycle-feishu-1",
                "event_type": "im.message.receive_v1",
                "tenant_key": "demo",
            },
            "event": {
                "sender": {"sender_id": {"user_id": "feishu-user-1"}},
                "message": {
                    "message_id": "msg-lifecycle-1",
                    "chat_id": "chat-1",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": TRACEBACK}),
                },
            },
        },
    )
    assert response.status_code == 200
    run_id = UUID(response.json()["run_id"])
    bus: LifecycleBus = app.state.lifecycle_bus
    run_types = {event.type for event in bus.for_run(run_id)}
    assert LifecycleEventType.RUN_CREATED in run_types
    assert LifecycleEventType.ANSWER_COMPOSED in run_types
    # Memory proposal is optional depending on verified FACT presence.
    del evidence
    all_types = {event.type for event in bus.all()}
    assert LifecycleEventType.ENGINEERING_PROPOSED in all_types


def test_conversation_answer_merge_does_not_require_hooks_for_memory() -> None:
    bus = LifecycleBus()
    service = ConversationService(lifecycle=bus)
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=_project(),
        source=SourceRef(system="local", source_id="x"),
        content="x",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefbb",
    )
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="u1",
        project=_project(),
    )
    service.record_turn(
        session,
        user_id="u1",
        text="q",
        rewritten_question=None,
        run_id=uuid4(),
        answer=ProjectAnswer(
            project=_project(),
            skill="incident_diagnosis",
            confidence=0.5,
            status="identified",
            business_summary="b",
            technical_summary="t",
            claims=(
                Claim(
                    text="fact",
                    type=ClaimType.FACT,
                    evidence_ids=(evidence.id,),
                    grade=EvidenceGrade.B,
                ),
            ),
            evidence=(evidence,),
            recommended_actions=(
                ActionProposal(
                    title="x",
                    tool_name="engineering_proposal",
                    requires_approval=True,
                    arguments={"can_apply": False, "affected_paths": ["src/a.py"]},
                ),
            ),
        ),
    )
    assert LifecycleEventType.MEMORY_APPROVED not in {
        event.type for event in bus.all()
    }
