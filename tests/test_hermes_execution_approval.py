from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from project_lens.application.hermes_execution import HermesExecutionService
from project_lens.domain.approval import ApprovalKind, ApprovalRecord, ApprovalStatus
from project_lens.domain.models import ProjectRef, utc_now
from project_lens.runtime.execution_guard import ExecutionGuard
from project_lens.runtime.patch_plan import FilePatch, PatchPlan
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.worktree import IsolatedWorktree


def _plan() -> PatchPlan:
    return PatchPlan("fix", "guard", (FilePatch("src/app.py", "old", "new"),), ("python -c \"print(1)\"",))


def test_execution_requires_explicit_approval_and_feature_flag(tmp_path: Path) -> None:
    project = ProjectRef(tenant_id="demo", project_id="p")
    proposal_id = uuid4()
    approval = ApprovalRecord(
        kind=ApprovalKind.ENGINEERING_APPLY,
        proposal_id=proposal_id,
        project=project,
        requested_by="hermes",
        decision=ApprovalStatus.APPROVED,
    )
    service = HermesExecutionService(
        guard=ExecutionGuard(EngineeringPolicy(tmp_path, allowed_tests=("python",)), execution_enabled=False),
        worktree=IsolatedWorktree(EngineeringPolicy(tmp_path, allowed_tests=("python",))),
    )
    with pytest.raises(PermissionError, match="disabled"):
        service.execute_patch_plan(proposal_id=proposal_id, project=project, plan=_plan(), approval=approval)


def test_approved_execution_stays_in_isolated_worktree(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("old", encoding="utf-8")
    project = ProjectRef(tenant_id="demo", project_id="p")
    proposal_id = uuid4()
    approval = ApprovalRecord(
        kind=ApprovalKind.ENGINEERING_APPLY,
        proposal_id=proposal_id,
        project=project,
        requested_by="hermes",
        decision=ApprovalStatus.APPROVED,
        expires_at=utc_now() + timedelta(hours=1),
    )
    policy = EngineeringPolicy(tmp_path, allowed_tests=("python",), allow_apply=True)
    service = HermesExecutionService(guard=ExecutionGuard(policy, execution_enabled=True), worktree=IsolatedWorktree(policy))
    report = service.execute_patch_plan(proposal_id=proposal_id, project=project, plan=_plan(), approval=approval)
    assert report.test_passed is True
    assert report.execution_scope == "isolated_worktree"
    assert (tmp_path / "src" / "app.py").read_text(encoding="utf-8") == "old"
