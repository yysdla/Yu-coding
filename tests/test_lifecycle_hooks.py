"""Lifecycle hook bus coverage for run, compression, and memory approvals."""

from __future__ import annotations

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


def test_run_lifecycle_hooks_create_run_under_hermes() -> None:
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
    assert execute.status_code == 409
    assert "Hermes" in execute.json()["detail"]

    bus: LifecycleBus = app.state.lifecycle_bus
    types = {event.type for event in bus.for_run(run_id)}
    assert LifecycleEventType.RUN_CREATED in types


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
