"""Create memory proposals from verified answers without Feishu coupling."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from project_lens.domain.memory import MemoryProposal, MemoryType
from project_lens.domain.models import ClaimType, ProjectAnswer


def infer_memory_type(claim_text: str, *, skill: str | None = None) -> MemoryType:
    """Heuristic memory taxonomy for proposal routing; never writes memory by itself."""

    normalized = claim_text.casefold()
    if any(term in normalized for term in ("负责人", "owner", "谁负责")):
        return MemoryType.OWNER
    if any(term in normalized for term in ("复盘", "postmortem", "事后总结")):
        return MemoryType.POSTMORTEM
    if any(term in normalized for term in ("runbook", "手册", "操作步骤")):
        return MemoryType.RUNBOOK
    if any(term in normalized for term in ("决策", "decision", "拍板")):
        return MemoryType.DECISION
    if any(term in normalized for term in ("风险", "risk", "边界条件")):
        return MemoryType.RISK
    if any(term in normalized for term in ("业务规则", "business rule", "必须", "不得")):
        return MemoryType.BUSINESS_RULE
    if any(term in normalized for term in ("约定", "convention", "规范")):
        return MemoryType.TEAM_CONVENTION
    if skill == "architecture" or any(
        term in normalized for term in ("架构", "服务", "入口", "依赖", "architecture")
    ):
        return MemoryType.ARCHITECTURE_FACT
    return MemoryType.ARCHITECTURE_FACT


def propose_memory_from_answer(
    answer: ProjectAnswer,
    *,
    proposed_by: str,
    reason: str | None = None,
    allowed_approvers: tuple[str, ...] = (),
    expires_at: datetime | None = None,
    replaces_memory_id: UUID | None = None,
) -> MemoryProposal | None:
    """Return a pending proposal for the first evidence-backed FACT claim, if any."""

    fact = next(
        (
            claim
            for claim in answer.claims
            if claim.type == ClaimType.FACT and claim.evidence_ids
        ),
        None,
    )
    if fact is None:
        return None
    memory_type = infer_memory_type(fact.text, skill=answer.skill)
    resolved_reason = reason or (
        f"已验证 FACT（skill={answer.skill or 'unknown'}），"
        f"建议沉淀为 {memory_type.value}；人工确认前不写入正式 ProjectMemory。"
    )
    payload: dict[str, object] = {
        "project": answer.project,
        "proposed_by": proposed_by,
        "claim_text": fact.text,
        "claim_type": fact.type,
        "memory_type": memory_type,
        "evidence_ids": fact.evidence_ids,
        "reason": resolved_reason,
        "status": "pending",
        "allowed_approvers": allowed_approvers,
    }
    if expires_at is not None:
        payload["expires_at"] = expires_at
    if replaces_memory_id is not None:
        payload["replaces_memory_id"] = replaces_memory_id
    return MemoryProposal.model_validate(payload)
