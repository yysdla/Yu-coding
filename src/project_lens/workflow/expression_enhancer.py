"""Apply validated LLM expression output onto ProjectAnswer without weakening evidence rules."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from project_lens.domain.models import Claim, ClaimType, EvidenceGrade, ProjectAnswer
from project_lens.workflow.llm_schema import parse_llm_expression_output
from project_lens.workflow.model_adapter import ModelAdapterResult


def maybe_enhance_answer(
    answer: ProjectAnswer,
    adapter_result: ModelAdapterResult | None,
) -> tuple[ProjectAnswer, dict[str, Any]]:
    """Enhance summaries from live LLM content. Never enables Apply or writes memory."""

    audit: dict[str, Any] = {
        "enhanced": False,
        "allow_apply": False,
        "rejected_fact_drafts": 0,
        "hypotheses_added": 0,
        "schema_ok": False,
        "live_effective": False,
    }
    if adapter_result is None:
        return answer, audit
    audit["live_effective"] = bool(adapter_result.live_effective)
    # Only enhance from a successful live completion (not stub fallback prose).
    if not adapter_result.live_effective or adapter_result.status != "ok":
        return answer, audit
    if not adapter_result.content:
        return answer, audit

    parsed = parse_llm_expression_output(adapter_result.content)
    if parsed is None:
        return answer, audit
    audit["schema_ok"] = True
    if parsed.allow_apply:
        raise ValueError("expression enhancer refused allow_apply=True")

    evidence_ids = {str(item.id) for item in answer.evidence}
    unknowns = list(answer.unknowns)
    new_claims: list[Claim] = list(answer.claims)
    rejected = 0
    for draft in parsed.claim_drafts:
        cited = tuple(item for item in draft.evidence_ids if item in evidence_ids)
        if draft.claim_type.lower() == "fact" and not cited:
            rejected += 1
            unknowns.append(f"假设（缺证据，未采纳为事实）：{draft.text}")
            continue
        claim_type = ClaimType.INFERENCE
        grade = EvidenceGrade.C
        if draft.claim_type.lower() == "fact" and cited:
            claim_type = ClaimType.FACT
            grade = EvidenceGrade.B
        elif draft.claim_type.lower() == "unknown":
            claim_type = ClaimType.UNKNOWN
            grade = EvidenceGrade.UNKNOWN
            unknowns.append(draft.text)
            continue
        new_claims.append(
            Claim(
                text=draft.text,
                type=claim_type,
                evidence_ids=tuple(UUID(item) for item in cited),
                grade=grade,
            )
        )
    hypotheses_added = 0
    for hypothesis in parsed.hypotheses:
        text = hypothesis.strip()
        if text:
            unknowns.append(f"假设：{text}")
            hypotheses_added += 1

    business = parsed.business_summary or answer.business_summary
    technical = parsed.technical_summary or answer.technical_summary
    if parsed.product_summary:
        business = f"{parsed.product_summary.strip()} {business}".strip()

    enhanced = answer.model_copy(
        update={
            "business_summary": business,
            "technical_summary": technical,
            "claims": tuple(new_claims),
            "unknowns": tuple(dict.fromkeys(unknowns)),
        }
    )
    audit.update(
        {
            "enhanced": True,
            "rejected_fact_drafts": rejected,
            "hypotheses_added": hypotheses_added,
            "allow_apply": False,
        }
    )
    return enhanced, audit
