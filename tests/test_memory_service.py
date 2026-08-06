from datetime import datetime, timezone
from uuid import uuid4

from project_lens.application.memory_service import (
    infer_memory_type,
    propose_memory_from_answer,
)
from project_lens.domain.memory import MemoryType
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


def test_propose_memory_from_answer_uses_first_fact_with_evidence() -> None:
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        source=SourceRef(system="feishu_doc", source_id="doc-1"),
        content="order-service owner is Ada",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    answer = ProjectAnswer(
        project=evidence.project,
        status="identified",
        business_summary="owner known",
        technical_summary="owner known",
        claims=(
            Claim(
                text="order-service owner is Ada",
                type=ClaimType.FACT,
                evidence_ids=(evidence.id,),
                grade=EvidenceGrade.B,
            ),
            Claim(
                text="maybe related",
                type=ClaimType.INFERENCE,
                evidence_ids=(evidence.id,),
                grade=EvidenceGrade.C,
            ),
        ),
        evidence=(evidence,),
    )
    proposal = propose_memory_from_answer(answer, proposed_by="u1")
    assert proposal is not None
    assert proposal.claim_text == "order-service owner is Ada"
    assert proposal.evidence_ids == (evidence.id,)
    assert proposal.status == "pending"
    assert proposal.memory_type == MemoryType.OWNER
    assert "owner" in proposal.reason
    assert infer_memory_type("optional coupon is a business rule that must accept null") == (
        MemoryType.BUSINESS_RULE
    )


def test_propose_memory_from_answer_returns_none_without_fact_evidence() -> None:
    answer = ProjectAnswer(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        status="investigating",
        business_summary="unknown",
        technical_summary="unknown",
        claims=(
            Claim(
                text="missing fact",
                type=ClaimType.UNKNOWN,
                grade=EvidenceGrade.UNKNOWN,
            ),
        ),
    )
    assert propose_memory_from_answer(answer, proposed_by="u1") is None
    # FACT without evidence ids is invalid at model layer; inference alone is not proposable.
    evidence_id = uuid4()
    inference_only = ProjectAnswer(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        status="investigating",
        business_summary="guess",
        technical_summary="guess",
        claims=(
            Claim(
                text="guess only",
                type=ClaimType.INFERENCE,
                evidence_ids=(evidence_id,),
                grade=EvidenceGrade.C,
            ),
        ),
        evidence=(
            Evidence(
                id=evidence_id,
                type=EvidenceType.DOCUMENT,
                project=ProjectRef(tenant_id="demo", project_id="payment"),
                source=SourceRef(system="local", source_id="x"),
                content="guess only",
                observed_at=datetime.now(timezone.utc),
                access_scope="project:payment:read",
                content_hash="1234567890abcdefbb",
            ),
        ),
    )
    assert propose_memory_from_answer(inference_only, proposed_by="u1") is None
