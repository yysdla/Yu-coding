"""HTTP approval and isolated execution routes for Hermes proposals."""
from __future__ import annotations
from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from project_lens.domain.approval import ApprovalKind, ApprovalRecord
from project_lens.domain.models import ProjectRef, utc_now
from project_lens.application.hermes_execution import HermesExecutionService
from project_lens.runtime.execution_guard import ExecutionGuard
from project_lens.runtime.patch_plan import FilePatch, PatchPlan
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.worktree import IsolatedWorktree

router = APIRouter(tags=["hermes-execution"])

class EngineeringApprovalBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID
    project: ProjectRef
    requested_by: str = Field(min_length=1, max_length=100)
    allowed_approvers: tuple[str, ...] = ()
    expires_in_seconds: int = Field(default=3600, ge=60, le=604800)

class EngineeringDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool
    decided_by: str = Field(min_length=1, max_length=100)

class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    old_text: str = ""
    new_text: str

class HermesExecuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: UUID
    approval_id: UUID
    project: ProjectRef
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)
    patches: tuple[PatchBody, ...] = Field(min_length=1, max_length=50)
    test_commands: tuple[str, ...] = ()

@router.post("/hermes/engineering-approvals", status_code=status.HTTP_201_CREATED)
def create_engineering_approval(payload: EngineeringApprovalBody, request: Request) -> dict[str, object]:
    store = request.app.state.engineering_approval_store
    approval = ApprovalRecord(kind=ApprovalKind.ENGINEERING_APPLY, proposal_id=payload.proposal_id, project=payload.project, requested_by=payload.requested_by, allowed_approvers=tuple(sorted(set(payload.allowed_approvers))), expires_at=utc_now() + timedelta(seconds=payload.expires_in_seconds))
    return store.create(approval).model_dump(mode="json")

@router.post("/hermes/engineering-approvals/{approval_id}/decision")
def decide_engineering_approval(approval_id: UUID, payload: EngineeringDecisionBody, request: Request) -> dict[str, object]:
    try:
        result = request.app.state.engineering_approval_store.decide(approval_id, approved=payload.approved, decided_by=payload.decided_by)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    return result.model_dump(mode="json")

@router.post("/hermes/execute")
def execute_hermes_plan(payload: HermesExecuteBody, request: Request) -> dict[str, object]:
    if not bool(getattr(request.app.state, "hermes_execution_enabled", False)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Hermes engineering execution is disabled")
    approval = request.app.state.engineering_approval_store.get(payload.approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="approval not found")
    try:
        space = request.app.state.project_registry.require(payload.project.tenant_id, payload.project.project_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found") from exc
    root = space.primary_repository_root
    if root is not None and root.name == "src":
        root = root.parent
    if root is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="project has no repository root")
    policy = EngineeringPolicy(project_root=root, allowed_path_prefixes=("src/", "tests/"), allowed_tests=("pytest", "python"), allow_apply=True)
    service = HermesExecutionService(guard=ExecutionGuard(policy, execution_enabled=True), worktree=IsolatedWorktree(policy))
    plan = PatchPlan(title=payload.title, rationale=payload.rationale, patches=tuple(FilePatch(item.path, item.old_text, item.new_text) for item in payload.patches), test_commands=payload.test_commands or ("pytest -q",))
    try:
        report = service.execute_patch_plan(proposal_id=payload.proposal_id, project=payload.project, plan=plan, approval=approval)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return {"proposal_id": str(report.proposal_id), "project": report.project.model_dump(mode="json"), "applied_paths": list(report.applied_paths), "test_passed": report.test_passed, "test_output": report.test_output, "diff_text": report.diff_text, "worktree_root": report.worktree_root, "execution_scope": report.execution_scope, "main_workspace_modified": False}
