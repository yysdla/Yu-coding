"""ContextPack assembly and serialization."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from project_lens.application.conversation_service import ConversationService
from project_lens.context.models import AccessContext
from project_lens.domain.conversation import ConversationTurn, empty_summary
from project_lens.domain.memory import ProjectMemory
from project_lens.domain.models import (
    AgentRun,
    Evidence,
    EvidenceType,
    ProjectRef,
    SourceRef,
)
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
from project_lens.integrations.feishu.identity import parse_project_bindings
from project_lens.integrations.feishu.security import FeishuRequestVerifier
from project_lens.main import create_app
from project_lens.workflow.context_pack import (
    AnchorContext,
    ContextPack,
    TaskScratchpad,
    build_context_pack,
)
from project_lens.workflow.models import ResolvedProject


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _evidence() -> Evidence:
    return Evidence(
        type=EvidenceType.DOCUMENT,
        project=_project(),
        source=SourceRef(system="local", source_id="doc-1"),
        content="order-service owner is Ada",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )


def test_build_context_pack_assembles_layers() -> None:
    project = _project()
    run = AgentRun(
        project=project,
        user_id="user-1",
        channel_id="chat-1",
        question="最近故障是什么？",
    )
    resolved = ResolvedProject(
        project=project,
        access_scope="project:payment:read",
        resolution="explicit_project_reference",
    )
    access = AccessContext(
        tenant_id="demo",
        user_id="user-1",
        permissions=frozenset({"project:payment:read"}),
    )
    service = ConversationService()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
    )
    session = service.record_turn(
        session,
        user_id="user-1",
        text="traceback...",
        rewritten_question=None,
        run_id=uuid4(),
    )
    session = session.model_copy(
        update={
            "task_scratchpad": {
                "phase": "Validate",
                "files_read": ["src/order_service.py"],
                "allow_apply": False,
            }
        }
    )
    evidence = _evidence()
    memory = ProjectMemory(
        project=project,
        text="Ada owns order-service",
        evidence_ids=(evidence.id,),
        approved_by="reviewer",
    )

    pack = build_context_pack(
        run=run,
        resolved=resolved,
        access=access,
        session=session,
        evidence=(evidence,),
        memories=(memory,),
        skill="incident_diagnosis",
        allow_apply=False,
    )

    assert isinstance(pack.anchors, AnchorContext)
    assert pack.anchors.project == project
    assert pack.anchors.access_scope == "project:payment:read"
    assert pack.anchors.allow_apply is False
    assert pack.anchors.policy_version == "v1"
    assert pack.anchors.tool_boundary == "tool_gateway"
    assert pack.anchors.session_id == session.session_id
    assert len(pack.recent_turns) == 1
    assert isinstance(pack.recent_turns[0], ConversationTurn)
    assert pack.session_summary is not None
    assert pack.session_summary.project == project
    assert pack.task_state is not None
    assert pack.task_state.phase == "Validate"
    assert pack.task_state.files_read == ("src/order_service.py",)
    assert pack.task_state.allow_apply is False
    assert pack.evidence_ids == (evidence.id,)
    assert pack.memory_ids == (memory.id,)


def test_context_pack_round_trip_serialization() -> None:
    project = _project()
    evidence = _evidence()
    pack = ContextPack(
        anchors=AnchorContext(
            project=project,
            access_scope="project:payment:read",
            access=AccessContext(
                tenant_id="demo",
                user_id="u1",
                permissions=frozenset({"project:payment:read"}),
            ),
            user_id="u1",
            run_id=uuid4(),
            trace_id=uuid4(),
            question="hello",
            skill="project_knowledge",
            allow_apply=False,
        ),
        recent_turns=(
            ConversationTurn(user_id="u1", text="hello", rewritten_question=None),
        ),
        session_summary=empty_summary(project),
        task_state=TaskScratchpad(phase="Collect", allow_apply=False),
        evidence=(evidence,),
        memories=(),
    )

    raw = pack.model_dump_json()
    restored = ContextPack.model_validate_json(raw)
    assert restored.anchors.run_id == pack.anchors.run_id
    assert restored.evidence_ids == pack.evidence_ids
    assert restored.recent_turns[0].text == "hello"
    assert restored.task_state is not None
    assert restored.task_state.phase == "Collect"
    assert restored.anchors.allow_apply is False


def test_context_pack_refuses_allow_apply() -> None:
    project = _project()
    run = AgentRun(project=project, user_id="u1", question="q")
    resolved = ResolvedProject(
        project=project,
        access_scope="project:payment:read",
        resolution="explicit",
    )
    access = AccessContext(tenant_id="demo", user_id="u1")
    with pytest.raises(ValueError, match="allow_apply"):
        build_context_pack(
            run=run,
            resolved=resolved,
            access=access,
            allow_apply=True,
        )


def test_session_summary_is_not_promoted_to_memory_in_pack() -> None:
    project = _project()
    run = AgentRun(project=project, user_id="u1", question="q")
    service = ConversationService()
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="u1",
        project=project,
    )
    pack = build_context_pack(
        run=run,
        resolved=ResolvedProject(
            project=project,
            access_scope="project:payment:read",
            resolution="explicit",
        ),
        access=AccessContext(tenant_id="demo", user_id="u1"),
        session=session,
        memories=(),
    )
    assert pack.session_summary is not None
    assert pack.memories == ()
    assert pack.memory_ids == ()


def test_audit_refs_keep_ids_without_full_bodies() -> None:
    project = _project()
    evidence = _evidence()
    pack = build_context_pack(
        run=AgentRun(project=project, user_id="u1", question="q"),
        resolved=ResolvedProject(
            project=project,
            access_scope="project:payment:read",
            resolution="explicit",
        ),
        access=AccessContext(tenant_id="demo", user_id="u1"),
        evidence=(evidence,),
        skill="architecture",
    )
    refs = pack.audit_refs()
    assert refs["allow_apply"] is False
    assert refs["access_scope"] == "project:payment:read"
    assert refs["evidence_ids"] == [str(evidence.id)]
    assert "order-service owner is Ada" not in str(refs)
