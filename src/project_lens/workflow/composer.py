"""Compose business and technical views from one verified fact set."""

from __future__ import annotations

from project_lens.context.models import EvidenceBundle
from project_lens.domain.models import ActionProposal, ProjectAnswer, ProjectRef
from project_lens.workflow.models import AnalysisResult, AnswerAudience, VerificationResult


class AnswerComposer:
    def compose(
        self,
        project: ProjectRef,
        analysis: AnalysisResult,
        verification: VerificationResult,
        bundle: EvidenceBundle,
    ) -> ProjectAnswer:
        candidate_by_text = {item.text: item for item in analysis.candidates}
        business = [
            claim.text
            for claim in verification.claims
            if candidate_by_text[claim.text].audience
            in {AnswerAudience.BUSINESS, AnswerAudience.BOTH}
        ]
        technical = [
            claim.text
            for claim in verification.claims
            if candidate_by_text[claim.text].audience
            in {AnswerAudience.TECHNICAL, AnswerAudience.BOTH}
        ]
        unknowns = list(analysis.unknowns)
        unknowns.extend(f"未采纳结论：{issue.reason}" for issue in verification.issues)

        referenced_ids = {
            evidence_id
            for claim in verification.claims
            for evidence_id in claim.evidence_ids
        }
        referenced_ids.update(
            evidence_id
            for action in verification.actions
            for evidence_id in action.evidence_ids
        )
        evidence = tuple(item for item in bundle.evidence if item.id in referenced_ids)
        actions = tuple(
            ActionProposal(
                title=item.title,
                description=item.description,
                requires_approval=True,
            )
            for item in verification.actions
        )

        return ProjectAnswer(
            project=project,
            skill=analysis.skill.value,
            confidence=_estimate_confidence(verification, bundle, tuple(unknowns)),
            status="investigating" if unknowns else "identified",
            business_summary=" ".join(business) or "当前证据不足，尚不能确认业务影响。",
            technical_summary=" ".join(technical) or "当前证据不足，尚不能定位技术原因。",
            claims=verification.claims,
            evidence=evidence,
            unknowns=tuple(dict.fromkeys(unknowns)),
            recommended_actions=actions,
        )


def _estimate_confidence(
    verification: VerificationResult,
    bundle: EvidenceBundle,
    unknowns: tuple[str, ...],
) -> float:
    if not bundle.evidence:
        return 0.0
    accepted_items = len(verification.claims) + len(verification.actions)
    base = 0.35 + min(0.35, len(bundle.evidence) * 0.05)
    base += min(0.25, accepted_items * 0.04)
    base -= min(0.25, len(verification.issues) * 0.04)
    base -= min(0.15, len(unknowns) * 0.02)
    return round(max(0.0, min(0.95, base)), 2)
