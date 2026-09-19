"""Memory approval tools with tool-spec gates and append-only audit.

create_memory_proposal never writes ProjectMemory.
decide_memory_proposal is HUMAN_CONTROLLED (RiskClass.APPLY on APPROVAL lane) —
it may write approved memory after a human decision, but is not Engineering Apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from project_lens.context.memory_store import MemoryStore
from project_lens.domain.memory import MemoryProposal, ProjectMemory
from project_lens.domain.models import ProjectRef
from project_lens.runtime.policy import RiskClass
from project_lens.runtime.tool_gateway import ToolAuditEvent
from project_lens.runtime.tool_specs import ToolLane, assert_tool_callable, list_tool_specs


@dataclass
class MemoryApprovalGateway:
    """Thin audit facade over MemoryStore create/decide proposal tools."""

    store: MemoryStore
    allow_apply: bool = False
    audit_events: list[ToolAuditEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.allow_apply:
            raise ValueError(
                "MemoryApprovalGateway refuses engineering allow_apply=True; "
                "memory decide is approval-lane, not patch Apply"
            )

    def list_approval_specs(self):
        return list_tool_specs(category=ToolLane.APPROVAL)

    def create_memory_proposal(self, proposal: MemoryProposal) -> MemoryProposal:
        assert_tool_callable("create_memory_proposal", allow_apply=False)
        try:
            created = self.store.create_proposal(proposal)
        except Exception as exc:
            self._audit(
                "create_memory_proposal",
                RiskClass.VALIDATE,
                {
                    "project": _project_ref(proposal.project),
                    "proposed_by": proposal.proposed_by,
                    "memory_type": proposal.memory_type.value,
                    "evidence_count": len(proposal.evidence_ids),
                },
                str(exc),
                False,
            )
            raise
        self._audit(
            "create_memory_proposal",
            RiskClass.VALIDATE,
            {
                "project": _project_ref(created.project),
                "proposal_id": str(created.id),
                "proposed_by": created.proposed_by,
                "memory_type": created.memory_type.value,
                "evidence_count": len(created.evidence_ids),
                "writes_project_memory": False,
            },
            f"proposal_id={created.id} status=pending",
            True,
        )
        return created

    def decide_memory_proposal(
        self,
        proposal_id: UUID,
        *,
        approved: bool,
        decided_by: str,
    ) -> tuple[MemoryProposal, ProjectMemory | None]:
        assert_tool_callable("decide_memory_proposal", allow_apply=False)
        arguments: dict[str, Any] = {
            "proposal_id": str(proposal_id),
            "approved": approved,
            "decided_by": decided_by,
            "engineering_apply": False,
        }
        try:
            current = self.store.get_proposal(proposal_id)
            if approved and current is not None and current.replaces_memory_id is not None:
                proposal, memory, _old = self.store.approve_replacement(
                    proposal_id,
                    decided_by=decided_by,
                )
            else:
                proposal, memory = self.store.decide_proposal(
                    proposal_id,
                    approved=approved,
                    decided_by=decided_by,
                )
        except Exception as exc:
            self._audit(
                "decide_memory_proposal",
                RiskClass.APPLY,
                arguments,
                str(exc),
                False,
            )
            raise
        arguments.update(
            {
                "project": _project_ref(proposal.project),
                "status": proposal.status,
                "memory_id": str(memory.id) if memory is not None else None,
                "writes_project_memory": memory is not None,
            }
        )
        self._audit(
            "decide_memory_proposal",
            RiskClass.APPLY,
            arguments,
            f"status={proposal.status} memory_id={arguments['memory_id']}",
            True,
        )
        return proposal, memory

    def list_memories(self, project: ProjectRef) -> tuple[ProjectMemory, ...]:
        """Pass-through list; not a mutating tool — no audit event."""

        return self.store.list_memories(project)

    def get_proposal(self, proposal_id: UUID) -> MemoryProposal | None:
        return self.store.get_proposal(proposal_id)

    def audit_summary(self) -> dict[str, Any]:
        names = [event.tool_name for event in self.audit_events]
        return {
            "memory_tool_calls": len(self.audit_events),
            "memory_tool_names": names,
            "allow_apply": False,
            "engineering_apply_events": [
                event.tool_name
                for event in self.audit_events
                if event.tool_name == "apply_patch_plan"
            ],
            "ok": all(event.ok for event in self.audit_events),
            "pending_creates": names.count("create_memory_proposal"),
            "decisions": names.count("decide_memory_proposal"),
        }

    def _audit(
        self,
        tool_name: str,
        risk_class: RiskClass,
        arguments: dict[str, Any],
        result: str,
        ok: bool,
    ) -> None:
        self.audit_events.append(
            ToolAuditEvent(
                id=uuid4(),
                tool_name=tool_name,
                risk_class=risk_class,
                arguments=arguments,
                result=result[:2_000],
                ok=ok,
            )
        )


def _project_ref(project: ProjectRef) -> dict[str, str | None]:
    return {
        "tenant_id": project.tenant_id,
        "project_id": project.project_id,
        "service": project.service,
        "environment": project.environment,
    }
