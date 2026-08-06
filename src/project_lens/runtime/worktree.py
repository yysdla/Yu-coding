"""Isolated worktree helpers for Validate-stage patch application."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from project_lens.runtime.patch_plan import PatchPlan
from project_lens.runtime.policy import EngineeringPolicy


@dataclass(frozen=True)
class WorktreeValidationResult:
    worktree_root: Path
    diff_text: str
    test_passed: bool
    test_output: str
    applied_paths: tuple[str, ...]


class IsolatedWorktree:
    def __init__(self, policy: EngineeringPolicy) -> None:
        self._policy = policy

    def validate_patch_plan(self, plan: PatchPlan) -> WorktreeValidationResult:
        for path in plan.affected_paths():
            self._policy.resolve_path(path)
        temp_root = Path(tempfile.mkdtemp(prefix="project-lens-worktree-"))
        try:
            self._mirror_allowed_tree(temp_root)
            applied: list[str] = []
            for patch in plan.patches:
                target = temp_root / patch.path
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    current = target.read_text(encoding="utf-8")
                    if patch.old_text and patch.old_text not in current:
                        raise ValueError(f"old_text not found in {patch.path}")
                    updated = (
                        current.replace(patch.old_text, patch.new_text, 1)
                        if patch.old_text
                        else patch.new_text
                    )
                else:
                    updated = patch.new_text
                target.write_text(updated, encoding="utf-8")
                applied.append(patch.path)
            diff_text = self._summarize_diff(temp_root, applied)
            test_passed, test_output = self._run_allowed_tests(temp_root, plan.test_commands)
            return WorktreeValidationResult(
                worktree_root=temp_root,
                diff_text=diff_text,
                test_passed=test_passed,
                test_output=test_output,
                applied_paths=tuple(applied),
            )
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

    def cleanup(self, result: WorktreeValidationResult) -> None:
        shutil.rmtree(result.worktree_root, ignore_errors=True)

    def _mirror_allowed_tree(self, temp_root: Path) -> None:
        root = self._policy.project_root
        for prefix in self._policy.allowed_path_prefixes:
            source = root / prefix.rstrip("/")
            if not source.exists():
                continue
            destination = temp_root / prefix.rstrip("/")
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            else:
                shutil.copytree(source, destination, dirs_exist_ok=True)

    def _summarize_diff(self, temp_root: Path, paths: list[str]) -> str:
        chunks: list[str] = []
        for relative in paths:
            original = self._policy.project_root / relative
            updated = temp_root / relative
            before = original.read_text(encoding="utf-8") if original.exists() else ""
            after = updated.read_text(encoding="utf-8") if updated.exists() else ""
            chunks.append(f"--- a/{relative}\n+++ b/{relative}\n")
            if before != after:
                chunks.append(f"- lines:{len(before.splitlines())}\n+ lines:{len(after.splitlines())}\n")
                if before and after:
                    for old_line, new_line in zip(before.splitlines(), after.splitlines(), strict=False):
                        if old_line != new_line:
                            chunks.append(f"- {old_line}\n+ {new_line}\n")
                            break
                elif after:
                    chunks.append(f"+ {after.splitlines()[0] if after.splitlines() else ''}\n")
        return "".join(chunks) or "no diff"

    def _run_allowed_tests(
        self,
        temp_root: Path,
        commands: tuple[str, ...],
    ) -> tuple[bool, str]:
        outputs: list[str] = []
        for command in commands:
            binary = command.split()[0]
            if binary not in self._policy.allowed_tests:
                raise PermissionError(f"test command not allowed: {command}")
            # Validate stage records the intended command; execution is sandboxed and optional.
            # Prefer a dry local python -c true when pytest may be unavailable in worktree copy.
            try:
                completed = subprocess.run(
                    command,
                    cwd=temp_root,
                    shell=True,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                outputs.append(completed.stdout + completed.stderr)
                if completed.returncode != 0:
                    return False, "\n".join(outputs)
            except (OSError, subprocess.TimeoutExpired) as exc:
                outputs.append(str(exc))
                return False, "\n".join(outputs)
        return True, "\n".join(outputs) or "tests passed"
