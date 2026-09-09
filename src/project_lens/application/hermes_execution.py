"""Approval-gated, isolated execution for Hermes engineering proposals."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from project_lens.domain.approval import ApprovalRecord
from project_lens.domain.models import ProjectRef
from project_lens.runtime.execution_guard import ExecutionGuard
from project_lens.runtime.patch_plan import PatchPlan
from project_lens.runtime.worktree import IsolatedWorktree, WorktreeValidationResult


@dataclass(frozen=True)
class HermesExecutionReport:
    proposal_id: UUID
    project: ProjectRef
    applied_paths: tuple[str, ...]
    test_passed: bool
    test_output: str
    diff_text: str
    worktree_root: str
    execution_scope: str = "isolated_worktree"


class HermesExecutionService:
    """Execute only approved plans inside a disposable isolated worktree."""

    def __init__(self, *, guard: ExecutionGuard, worktree: IsolatedWorktree) -> None:
        self._guard = guard
        self._worktree = worktree

    def execute_patch_plan(
        self,
        *,
        proposal_id: UUID,
        project: ProjectRef,
        plan: PatchPlan,
        approval: ApprovalRecord | None,
    ) -> HermesExecutionReport:
        self._guard.assert_allowed(
            approval=approval,
            proposal_id=proposal_id,
            project=project,
        )
        result: WorktreeValidationResult = self._worktree.validate_patch_plan(plan)
        return HermesExecutionReport(
            proposal_id=proposal_id,
            project=project,
            applied_paths=result.applied_paths,
            test_passed=result.test_passed,
            test_output=result.test_output[:10_000],
            diff_text=result.diff_text[:20_000],
            worktree_root=str(result.worktree_root),
        )

