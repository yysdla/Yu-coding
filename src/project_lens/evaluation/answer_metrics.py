"""Quality and safety metrics for generated project answers."""

from __future__ import annotations

from project_lens.domain.models import ClaimType, ProjectAnswer


def answer_metrics(answer: ProjectAnswer) -> dict[str, float | int | bool]:
    supported_claims = sum(
        1
        for claim in answer.claims
        if claim.type in {ClaimType.FACT, ClaimType.INFERENCE} and claim.evidence_ids
    )
    factual_claims = sum(
        1 for claim in answer.claims if claim.type in {ClaimType.FACT, ClaimType.INFERENCE}
    )
    citation_coverage = supported_claims / factual_claims if factual_claims else 1.0
    evidence_ids = {item.id for item in answer.evidence}
    cited_ids = {
        evidence_id for claim in answer.claims for evidence_id in claim.evidence_ids
    }
    return {
        "claim_count": len(answer.claims),
        "factual_claim_count": factual_claims,
        "citation_coverage": citation_coverage,
        "evidence_precision": len(cited_ids & evidence_ids) / len(cited_ids) if cited_ids else 1.0,
        "unknown_count": len(answer.unknowns),
        "all_actions_require_approval": all(
            action.requires_approval for action in answer.recommended_actions
        ),
    }
