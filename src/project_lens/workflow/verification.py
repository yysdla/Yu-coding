"""Independent structural verifier for analysis-agent output."""

from __future__ import annotations

from project_lens.context.models import EvidenceBundle
from project_lens.domain.models import Claim, ClaimType, EvidenceGrade, ProjectRef
from project_lens.workflow.models import AnalysisResult, VerificationIssue, VerificationResult


class VerificationAgent:
    def verify(
        self,
        analysis: AnalysisResult,
        bundle: EvidenceBundle,
        project: ProjectRef,
    ) -> VerificationResult:
        evidence_by_id = {item.id: item for item in bundle.evidence}
        claims: list[Claim] = []
        actions = []
        issues: list[VerificationIssue] = []

        for candidate in analysis.candidates:
            referenced = [evidence_by_id.get(item) for item in candidate.evidence_ids]
            reason = _claim_rejection_reason(
                candidate.type,
                candidate.grade,
                candidate.support_terms,
                referenced,
                project,
            )
            if reason:
                issues.append(VerificationIssue(item=candidate.text, reason=reason))
                continue
            claims.append(
                Claim(
                    text=candidate.text,
                    type=candidate.type,
                    evidence_ids=candidate.evidence_ids,
                    grade=candidate.grade,
                )
            )

        for action in analysis.actions:
            referenced = [evidence_by_id.get(item) for item in action.evidence_ids]
            if not referenced or any(item is None for item in referenced):
                issues.append(
                    VerificationIssue(item=action.title, reason="action lacks known evidence")
                )
                continue
            if any(item.project != project for item in referenced if item):
                issues.append(
                    VerificationIssue(item=action.title, reason="action cites another project")
                )
                continue
            if not _terms_supported(action.support_terms, referenced):
                issues.append(
                    VerificationIssue(
                        item=action.title,
                        reason="action is not supported by the cited evidence content",
                    )
                )
                continue
            actions.append(action)

        return VerificationResult(
            claims=tuple(claims), actions=tuple(actions), issues=tuple(issues)
        )


def _claim_rejection_reason(
    claim_type: ClaimType,
    grade: EvidenceGrade,
    support_terms: tuple[str, ...],
    referenced: list,
    project: ProjectRef,
) -> str | None:
    if claim_type in {ClaimType.FACT, ClaimType.INFERENCE} and not referenced:
        return "factual or inferred claim lacks evidence"
    if any(item is None for item in referenced):
        return "claim cites evidence outside the retrieved bundle"
    if any(item.project != project for item in referenced if item):
        return "claim cites another project"
    if not _terms_supported(support_terms, referenced):
        return "claim is not supported by the cited evidence content"
    if claim_type == ClaimType.INFERENCE and grade != EvidenceGrade.C:
        return "inferences must use evidence grade C"
    if claim_type == ClaimType.UNKNOWN and grade != EvidenceGrade.UNKNOWN:
        return "unknown claims must use the unknown grade"
    return None


def _terms_supported(terms: tuple[str, ...], referenced: list) -> bool:
    if not terms:
        return False
    content = "\n".join(item.content for item in referenced if item).lower()
    return all(term.lower() in content for term in terms)
