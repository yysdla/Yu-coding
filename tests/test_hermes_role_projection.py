from datetime import datetime, timezone
from uuid import uuid4

from project_lens.domain.models import AgentRun, Claim, ClaimType, Evidence, EvidenceGrade, EvidenceType, EvidenceRef, ProjectAnswer, ProjectRef, SourceRef
from project_lens.integrations.feishu.cards import render_answer_card
from project_lens.project_space.policies import effective_scope_to_audit_dict, EffectiveAccessScope, RoleKind, AnswerDepth, VisibilityLevel


def test_hermes_verified_answer_uses_run_scope_for_role_projection() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="github", source_id="src/order.py"),
        content="order owner Ada",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef",
    )
    claim = Claim(
        text="order owner Ada",
        type=ClaimType.FACT,
        evidence_ids=(evidence.id,),
        grade=EvidenceGrade.B,
    )
    answer = ProjectAnswer(
        project=project,
        status="identified",
        business_summary="order owner Ada",
        technical_summary="order owner Ada",
        conclusion="order owner Ada",
        claims=(claim,), facts=(claim,), evidence=(evidence,),
        citations=(EvidenceRef(id=evidence.id, kind=evidence.type, source_uri="github:src/order.py"),),
    )
    scope = EffectiveAccessScope(
        actor_id="dev-1", chat_id="chat-1", project=project,
        role=RoleKind.DEVELOPER, readable_sources=("code",), allowed_tools=("search_context",),
        forbidden_sources=(), answer_depth=AnswerDepth.DETAILED,
        answer_style="technical", visibility_level=VisibilityLevel.TEAM_SHARED,
        identity_source="test", policy_version="v1", chat_type="group",
    )
    run = AgentRun(project=project, user_id="dev-1", channel_id="chat-1", question="risk?", runtime_access=effective_scope_to_audit_dict(scope))
    card = render_answer_card(run, answer)
    text = str(card)
    assert "技术影响" in text or "技术视图" in text
    assert "order owner Ada" in text
