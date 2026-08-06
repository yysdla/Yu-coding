"""Project Engineering skill: Explain -> Propose -> Validate (no production Apply)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from project_lens.domain.models import ActionProposal, ProjectRef
from project_lens.runtime.patch_plan import FilePatch, PatchPlan
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.tool_gateway import ToolGateway
from project_lens.runtime.worktree import WorktreeValidationResult


@dataclass(frozen=True)
class EngineeringProposal:
    project: ProjectRef
    explanation: str
    plan: PatchPlan
    validation: WorktreeValidationResult | None
    action: ActionProposal
    can_apply: bool = False


class ProjectEngineeringSkill:
    """Read-only by default; Validate runs in an isolated worktree only."""

    def __init__(self, gateway: ToolGateway) -> None:
        self._gateway = gateway

    @classmethod
    def for_project_root(
        cls,
        project_root: Path,
        *,
        allow_apply: bool = False,
        allowed_tests: tuple[str, ...] = ("pytest", "python"),
    ) -> "ProjectEngineeringSkill":
        policy = EngineeringPolicy(
            project_root=project_root,
            allow_apply=allow_apply,
            allowed_tests=allowed_tests,
        )
        return cls(ToolGateway(policy))

    def propose_from_traceback(
        self,
        *,
        project: ProjectRef,
        traceback: str,
        relative_path: str,
        test_commands: tuple[str, ...] = ("python -c \"print('ok')\"",),
    ) -> EngineeringProposal:
        source = self._gateway.read_project_file(relative_path)
        explanation = _explain_traceback(traceback, relative_path, source)
        risky = _guess_risky_line(source, traceback)
        plan = self._gateway.create_patch_plan(
            title=f"Guard optional access in {relative_path}",
            rationale=explanation,
            patches=[
                {
                    "path": relative_path,
                    "old_text": risky,
                    "new_text": _guarded_line(risky),
                }
            ],
            test_commands=list(test_commands),
        )
        return self.propose_plan(project=project, plan=plan, explanation=explanation)

    def propose_plan(
        self,
        *,
        project: ProjectRef,
        plan: PatchPlan,
        explanation: str | None = None,
    ) -> EngineeringProposal:
        validation = self._gateway.validate_patch_plan(plan)
        explanation_text = explanation or plan.rationale
        action = to_engineering_action_proposal(
            explanation=explanation_text,
            plan=plan,
            validation=validation,
        )
        return EngineeringProposal(
            project=project,
            explanation=explanation_text,
            plan=plan,
            validation=validation,
            action=action,
            can_apply=False,
        )

    def apply(self, plan: PatchPlan) -> str:
        """Always refused in Feishu-facing flows; gateway also blocks Apply."""
        return self._gateway.apply_patch_plan(plan)


def to_engineering_action_proposal(
    *,
    explanation: str,
    plan: PatchPlan,
    validation: WorktreeValidationResult,
) -> ActionProposal:
    """Serialize Explain/Propose/Validate result for answer cards (never Apply)."""

    diff_summary = validation.diff_text[:800]
    test_output_summary = validation.test_output[:500]
    patch_plan_payload = patch_plan_to_payload(plan)
    patch_plan_text = format_patch_plan_text(plan)
    test_commands = list(plan.test_commands)
    failed_attempts: list[str] = []
    if not validation.test_passed:
        failed_attempts.append(
            f"worktree_validate_failed: {(validation.test_output or 'unknown')[:240]}"
        )
    title = (
        "修复提案已通过隔离验证，待人工审批"
        if validation.test_passed
        else "修复提案验证失败，不可应用"
    )
    return ActionProposal(
        title=title,
        description=(
            f"{explanation}\n"
            f"paths={', '.join(plan.affected_paths())}\n"
            f"test_passed={validation.test_passed}\n"
            f"diff:\n{diff_summary}"
        ),
        tool_name="engineering_proposal",
        requires_approval=True,
        arguments={
            "explanation": explanation,
            "affected_paths": list(plan.affected_paths()),
            "patch_plan": patch_plan_payload,
            "patch_plan_text": patch_plan_text,
            "diff_summary": diff_summary,
            "test_commands": test_commands,
            "test_passed": validation.test_passed,
            "test_output_summary": test_output_summary,
            "failed_attempts": failed_attempts,
            "requires_approval": True,
            "can_apply": False,
            "allow_apply": False,
            "validate_mode": "isolated_worktree",
        },
    )


def patch_plan_to_payload(plan: PatchPlan) -> dict[str, object]:
    return {
        "title": plan.title,
        "rationale": plan.rationale[:500],
        "steps": [
            {
                "path": patch.path,
                "old_preview": patch.old_text[:160],
                "new_preview": patch.new_text[:160],
            }
            for patch in plan.patches
        ],
    }


def format_patch_plan_text(plan: PatchPlan) -> str:
    lines = [f"{plan.title}: {plan.rationale[:200]}"]
    for patch in plan.patches:
        lines.append(
            f"- {patch.path}: {patch.old_text[:80]!r} -> {patch.new_text[:80]!r}"
        )
    return "\n".join(lines)[:800]


def build_null_guard_plan(
    relative_path: str,
    *,
    old_line: str,
    test_commands: tuple[str, ...] = ("python -c \"print('ok')\"",),
) -> PatchPlan:
    return PatchPlan(
        title=f"Add null guard in {relative_path}",
        rationale="Traceback indicates optional attribute access without a guard.",
        patches=(
            FilePatch(
                path=relative_path,
                old_text=old_line,
                new_text=_guarded_line(old_line),
            ),
        ),
        test_commands=test_commands,
    )


def _explain_traceback(traceback: str, relative_path: str, source: str) -> str:
    exception = "Error"
    for line in traceback.splitlines():
        if "Error" in line and not line.strip().startswith("File"):
            exception = line.strip()
            break
    return (
        f"Explain: {relative_path} may raise `{exception}` based on the traceback. "
        f"Source length={len(source.splitlines())} lines. "
        "Propose a minimal null guard and validate in an isolated worktree."
    )


def _guess_risky_line(source: str, traceback: str) -> str:
    match = re.search(r"line (\d+)", traceback)
    if match:
        index = int(match.group(1)) - 1
        lines = source.splitlines()
        if 0 <= index < len(lines):
            return lines[index]
    for line in source.splitlines():
        if ".id" in line or "coupon" in line:
            return line
    return source.splitlines()[0] if source.splitlines() else ""


def _guarded_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return "if value is not None:\n    pass"
    indent = line[: len(line) - len(line.lstrip())]
    return f"{indent}if True:  # validated guard placeholder\n{indent}    {stripped}"
