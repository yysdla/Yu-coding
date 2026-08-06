"""Harness replay + probe report evaluation."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from project_lens.application.conversation_service import ConversationService
from project_lens.application.feishu_doc_sync import FeishuDocumentSyncService
from project_lens.application.feishu_doc_sync_runner import sync_registered_projects
from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.memory import MemoryProposal
from project_lens.domain.models import ClaimType, ProjectRef
from project_lens.evaluation.harness_probes import (
    probe_approval_safety,
    probe_memory_boundary,
    run_harness_probes,
)
from project_lens.evaluation.harness_replay import ReplayStep, replay_harness_conversation
from project_lens.main import create_app
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.tool_gateway import ToolGateway

ROOT = Path(__file__).parents[1]
DEMO = ROOT / "examples" / "payment_service"

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


@pytest.mark.asyncio
async def test_replay_multi_turn_passes_harness_probes() -> None:
    app = create_app()
    # Force a short L1 window so compression probe exercises a real cycle.
    app.state.conversation_service = ConversationService(
        recent_turn_limit=2,
        lifecycle=app.state.lifecycle_bus,
    )
    project = _project()
    report = await replay_harness_conversation(
        run_service=app.state.run_service,
        conversation=app.state.conversation_service,
        project=project,
        steps=(
            ReplayStep(question=TRACEBACK),
            ReplayStep(question="那是谁改的？"),
            ReplayStep(question="影响哪里？"),
        ),
        memory_store=app.state.memory_store,
        context_engine=app.state.context_engine,
        evidence_index=app.state.evidence_index,
        lifecycle=app.state.lifecycle_bus,
    )
    assert report.passed, report.as_dict()
    assert len(report.run_ids) == 3
    assert report.steps[1]["followup_rewrite"]
    assert "故障诊断" in (report.steps[1]["followup_rewrite"] or "")
    probe_names = {item.name for item in report.probes.probes}
    assert {
        "evidence_safety",
        "approval_safety",
        "memory_boundary",
        "ops_boundary",
        "compression_quality",
        "context_recall",
        "artifact_trail",
        "lifecycle_trail",
        "context_prompt_trail",
        "read_gateway_trail",
    } <= probe_names
    assert all(item.passed for item in report.probes.probes)
    assert report.observability["模型看到了什么"]["layers"] == [
        "L0",
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
    ]
    assert report.observability["如果回答错了，能不能 replay"]["used_prompt"] is True


def test_probe_memory_boundary_blocks_pending_leak() -> None:
    store = InMemoryMemoryStore()
    project = _project()
    proposal = store.create_proposal(
        MemoryProposal(
            project=project,
            proposed_by="u1",
            claim_text="owner is Ada",
            claim_type=ClaimType.FACT,
            evidence_ids=(uuid4(),),
        )
    )
    result = probe_memory_boundary(
        store,
        project=project,
        pending_proposal_ids=(proposal.id,),
    )
    assert result.passed is True
    assert store.list_memories(project) == ()


def test_probe_approval_safety_with_gateway() -> None:
    gateway = ToolGateway(EngineeringPolicy(project_root=DEMO, allow_apply=False))
    plan = gateway.create_patch_plan(
        title="noop",
        rationale="probe",
        patches=[
            {
                "path": "src/order_service.py",
                "old_text": "coupon_id = request.coupon.id",
                "new_text": "coupon_id = request.coupon.id",
            }
        ],
    )
    with pytest.raises(PermissionError):
        gateway.apply_patch_plan(plan)
    result = probe_approval_safety(gateway=gateway)
    assert result.passed is True


def test_doc_sync_runner_emits_lifecycle_hooks() -> None:
    from project_lens.application.feishu_doc_sync_status import InMemoryFeishuDocSyncStatusStore
    from project_lens.context.bootstrap import LocalContextSources, LocalProjectRegistration
    from project_lens.integrations.feishu.docs_client import FeishuDocClient
    from project_lens.integrations.feishu.http_adapter import FeishuTenantTokenProvider
    from tests.test_feishu_doc_sync import RecordingTransport

    transport = RecordingTransport(
        {
            "/documents/docx_probe/raw_content": {
                "code": 0,
                "data": {"content": "doc sync lifecycle probe content"},
            },
            "/documents/docx_probe": {
                "code": 0,
                "data": {
                    "document": {
                        "document_id": "docx_probe",
                        "title": "Probe",
                        "revision_id": 1,
                    }
                },
            },
        }
    )
    token_provider = FeishuTenantTokenProvider(
        app_id="app",
        app_secret="secret",
        transport=transport,
    )
    bus = LifecycleBus()
    summary = sync_registered_projects(
        FeishuDocumentSyncService(
            client=FeishuDocClient(token_provider=token_provider, transport=transport),
            index=InMemoryEvidenceIndex(),
            status_store=InMemoryFeishuDocSyncStatusStore(),
        ),
        (
            LocalProjectRegistration(
                project=_project(),
                sources=LocalContextSources(
                    repository_root=DEMO / "src",
                    feishu_doc_tokens=("docx_probe",),
                ),
                access_scope="project:payment:read",
            ),
        ),
        lifecycle=bus,
    )
    assert summary.indexed >= 1
    types = {event.type for event in bus.all()}
    assert LifecycleEventType.DOC_SYNC_STARTED in types
    assert LifecycleEventType.DOC_SYNC_COMPLETED in types


def test_run_harness_probes_from_live_app_answer() -> None:
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
    run_id = create.json()["run_id"]
    executed = client.post(f"/api/v1/runs/{run_id}/execute")
    assert executed.status_code == 200
    run = app.state.run_service.get(__import__("uuid").UUID(run_id))
    assert run is not None and run.answer is not None
    report = run_harness_probes(
        answer=run.answer,
        memory_store=app.state.memory_store,
        context_engine=app.state.context_engine,
        evidence_index=app.state.evidence_index,
        lifecycle=app.state.lifecycle_bus,
        session=app.state.conversation_service.get_or_create(
            tenant_id="demo",
            chat_id="probe-chat",
            user_id="u1",
            project=_project(),
        ),
    )
    by_name = {item.name: item for item in report.probes}
    assert by_name["evidence_safety"].passed
    assert by_name["approval_safety"].passed
    assert by_name["ops_boundary"].passed
    assert by_name["lifecycle_trail"].passed
