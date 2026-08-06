"""Deterministic ContextPack -> ContextPrompt renderer."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from project_lens.context.models import AccessContext
from project_lens.domain.conversation import (
    ConversationTurn,
    PinnedIds,
    empty_summary,
)
from project_lens.domain.memory import ProjectMemory
from project_lens.domain.models import (
    AgentRun,
    Evidence,
    EvidenceType,
    ProjectRef,
    SourceRef,
)
from project_lens.workflow.context_pack import (
    ContextPack,
    TaskScratchpad,
    build_context_pack,
)
from project_lens.workflow.context_prompt import (
    DEFAULT_EVIDENCE_SNIPPET_CHARS,
    clip_evidence_snippet,
    render_context_prompt,
)
from project_lens.workflow.models import ResolvedProject


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _pack(*, long_evidence: bool = False) -> ContextPack:
    project = _project()
    run = AgentRun(
        project=project,
        user_id="user-1",
        channel_id="chat-1",
        question="影响哪里？",
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
    summary = empty_summary(project).model_copy(
        update={
            "active_skill": "incident_diagnosis",
            "unknowns": ("owner unknown",),
            "next_actions": ("check git blame",),
            "pinned_ids": PinnedIds(
                evidence_ids=("ev-pinned",),
                file_paths=("src/order_service.py",),
                proposal_ids=("prop-1",),
            ),
            "session_intent": "diagnose coupon AttributeError",
        }
    )
    from project_lens.domain.conversation import ConversationSession

    session = ConversationSession(
        tenant_id="demo",
        chat_id="chat-1",
        user_id="user-1",
        project=project,
        recent_turns=(
            ConversationTurn(
                user_id="user-1",
                text="AttributeError on coupon",
                run_id=uuid4(),
            ),
        ),
        summary=summary,
        task_scratchpad={
            "phase": "Validate",
            "files_read": ["src/order_service.py"],
            "test_results": ["pytest tests/test_order_service.py: passed"],
            "approval_status": "pending",
            "allow_apply": False,
        },
    )
    body = ("ORDER_SERVICE_OWNER_IS_ADA " * 40) if long_evidence else "Ada owns order-service"
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="local", source_id="doc-1"),
        content=body,
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
        metadata={"path": "docs/owners.md"},
    )
    memory = ProjectMemory(
        project=project,
        text="Ada owns order-service",
        evidence_ids=(evidence.id,),
        approved_by="approver-1",
    )
    return build_context_pack(
        run=run,
        resolved=resolved,
        access=access,
        session=session,
        evidence=(evidence,),
        memories=(memory,),
        skill="incident_diagnosis",
        allow_apply=False,
        task_state=TaskScratchpad(
            phase="Validate",
            files_read=("src/order_service.py",),
            test_results=("pytest tests/test_order_service.py: passed",),
            approval_status="pending",
            allow_apply=False,
            patch_plan="Add null guard for optional coupon",
        ),
    )


def test_prompt_includes_l0_policy_anchor() -> None:
    prompt = render_context_prompt(_pack())
    l0 = next(item for item in prompt.sections if item.layer == "L0")
    assert "access_scope=project:payment:read" in l0.content
    assert "allow_apply=False" in l0.content
    assert "tool_boundary=tool_gateway" in l0.content
    assert "policy_version=v1" in l0.content
    assert "project_id=payment" in l0.content
    assert "tool_policy_hash=" in l0.content
    assert "non_compressible=L0_anchors" in l0.content
    assert "provenance.evidence_source=read_context_gateway" in l0.content


def test_prompt_includes_summary_and_pinned_ids() -> None:
    prompt = render_context_prompt(_pack())
    l2 = next(item for item in prompt.sections if item.layer == "L2")
    assert "active_skill=incident_diagnosis" in l2.content
    assert "pinned.file_paths=src/order_service.py" in l2.content
    assert "pinned.evidence_ids=ev-pinned" in l2.content
    assert "pinned.proposal_ids=prop-1" in l2.content
    assert "unknowns=owner unknown" in l2.content
    assert "compression.non_compressible=L0_anchors" in l2.content
    assert "compression.compressed_turn_count=" in l2.content
    assert prompt.audit_refs["compression_manifest"]["non_compressible"]
    assert prompt.audit_refs["model_saw_layers"] == ["L0", "L1", "L2", "L3", "L4", "L5"]


def test_prompt_includes_task_scratchpad_files_and_tests() -> None:
    prompt = render_context_prompt(_pack())
    l3 = next(item for item in prompt.sections if item.layer == "L3")
    assert "phase=Validate" in l3.content
    assert "files_read=src/order_service.py" in l3.content
    assert "pytest tests/test_order_service.py: passed" in l3.content
    assert "approval_status=pending" in l3.content
    assert "allow_apply=False" in l3.content


def test_evidence_snippet_is_length_limited() -> None:
    prompt = render_context_prompt(_pack(long_evidence=True), evidence_snippet_chars=64)
    l4 = next(item for item in prompt.sections if item.layer == "L4")
    assert "snippet=" in l4.content
    snippet = l4.content.split("snippet=", 1)[1].split(" metadata_keys=", 1)[0]
    assert len(snippet) <= 64
    assert "…" in snippet
    assert len(clip_evidence_snippet("x" * 1000, max_chars=40)) <= 40


def test_prompt_never_sets_allow_apply_true() -> None:
    prompt = render_context_prompt(_pack())
    text = prompt.as_text()
    assert "allow_apply=True" not in text
    assert "allow_apply=False" in text
    assert prompt.audit_refs["allow_apply"] is False
    l5 = next(item for item in prompt.sections if item.layer == "L5")
    assert "approved_by=approver-1" in l5.content
    assert "Ada owns order-service" in l5.content


def test_audit_refs_do_not_leak_evidence_body() -> None:
    pack = _pack(long_evidence=True)
    full_body = pack.evidence[0].content
    prompt = render_context_prompt(pack, evidence_snippet_chars=48)
    serialized = str(prompt.audit_refs)
    assert full_body not in serialized
    assert "evidence_ids" in prompt.audit_refs
    assert full_body not in prompt.as_text() or len(full_body) > DEFAULT_EVIDENCE_SNIPPET_CHARS
    # Full body must not appear unclipped in L4.
    l4 = next(item for item in prompt.sections if item.layer == "L4")
    assert full_body not in l4.content


def test_render_refuses_allow_apply_true_pack() -> None:
    pack = _pack()
    bad = pack.model_copy(
        update={"anchors": pack.anchors.model_copy(update={"allow_apply": True})}
    )
    with pytest.raises(ValueError, match="allow_apply"):
        render_context_prompt(bad)
