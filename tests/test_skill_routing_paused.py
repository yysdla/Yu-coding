"""Legacy ProjectSkill routing is paused by default for Hermes."""

from project_lens.config import settings
from project_lens.workflow.skills import skill_routing_enabled


def test_project_skill_routing_paused_by_default() -> None:
    assert settings.project_skill_routing_enabled is False
    assert skill_routing_enabled() is False


def test_prepare_question_skips_followup_when_skill_routing_paused() -> None:
    from uuid import uuid4

    from project_lens.application.conversation_service import ConversationService
    from project_lens.domain.models import (
        Claim,
        ClaimType,
        Evidence,
        EvidenceGrade,
        EvidenceType,
        ProjectAnswer,
        ProjectRef,
        SourceRef,
    )
    from datetime import datetime, timezone

    service = ConversationService()
    project = ProjectRef(tenant_id="demo", project_id="payment")
    session = service.get_or_create(
        tenant_id="demo",
        chat_id="chat-skill-pause",
        user_id="u1",
        project=project,
    )
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local", source_id="x"),
        content="x",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    session = service.record_turn(
        session,
        user_id="u1",
        text="AttributeError on coupon",
        rewritten_question=None,
        run_id=uuid4(),
        answer=ProjectAnswer(
            project=project,
            skill="incident_diagnosis",
            confidence=0.5,
            status="identified",
            business_summary="incident",
            technical_summary="traceback",
            claims=(
                Claim(
                    text="error",
                    type=ClaimType.FACT,
                    evidence_ids=(evidence.id,),
                    grade=EvidenceGrade.B,
                ),
            ),
            evidence=(evidence,),
        ),
    )
    question, rewrite = service.prepare_question(session, "那是谁改的？")
    assert rewrite is None
    assert question == "那是谁改的？"
