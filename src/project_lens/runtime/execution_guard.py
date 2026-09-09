"""Approval and policy gate for Hermes-controlled engineering execution."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from uuid import UUID

from project_lens.domain.approval import ApprovalRecord, ApprovalStatus
from project_lens.domain.models import ProjectRef
from project_lens.runtime.policy import EngineeringPolicy


@dataclass(frozen=True)
class ExecutionGuard:
    """Fail closed unless an explicit, unexpired approval exists."""

    policy: EngineeringPolicy
    execution_enabled: bool = False

    def assert_allowed(
        self,
        *,
        approval: ApprovalRecord | None,
        proposal_id: UUID,
        project: ProjectRef,
    ) -> None:
        if not self.execution_enabled:
            raise PermissionError("Hermes engineering execution is disabled")
        self.policy.assert_can_apply()
        if approval is None:
            raise PermissionError("engineering execution requires an approval record")
        if approval.decision is not ApprovalStatus.APPROVED:
            raise PermissionError(f"approval is not approved: {approval.decision.value}")
        if approval.proposal_id != proposal_id:
            raise PermissionError("approval does not match the proposal")
        if approval.project.tenant_id != project.tenant_id or approval.project.project_id != project.project_id:
            raise PermissionError("approval project does not match execution project")
        if approval.expires_at is not None:
            expires = approval.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= datetime.now(timezone.utc):
                raise PermissionError("approval has expired")

