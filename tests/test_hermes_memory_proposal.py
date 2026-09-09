from datetime import datetime, timezone
from uuid import uuid4

from project_lens.application.memory_service import propose_memory_from_answer
from project_lens.domain.models import Claim, ClaimType, Evidence, EvidenceGrade, EvidenceType, ProjectAnswer, ProjectRef, SourceRef


def test_verified_hermes_answer_can_enter_memory_proposal_flow() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="feishu_doc", source_id="doc-1"),
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
        business_summary=claim.text,
        technical_summary=claim.text,
        claims=(claim,),
        facts=(claim,),
        evidence=(evidence,),
    )

    proposal = propose_memory_from_answer(answer, proposed_by="user-1")

    assert proposal is not None
    assert proposal.status == "pending"
    assert proposal.evidence_ids == (evidence.id,)
