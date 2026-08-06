from pathlib import Path

import pytest

from project_lens.runtime.patch_plan import FilePatch, PatchPlan
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.worktree import IsolatedWorktree

DEMO_SRC = Path(__file__).parents[1] / "examples" / "payment_service"


def test_isolated_worktree_applies_patch_and_returns_diff() -> None:
    policy = EngineeringPolicy(project_root=DEMO_SRC, allowed_tests=("python",))
    worktree = IsolatedWorktree(policy)
    plan = PatchPlan(
        title="guard",
        rationale="test",
        patches=(
            FilePatch(
                path="src/order_service.py",
                old_text="coupon_id = request.coupon.id",
                new_text="coupon_id = request.coupon.id  # patched",
            ),
        ),
        test_commands=('python -c "print(\'ok\')"',),
    )
    result = worktree.validate_patch_plan(plan)
    try:
        assert "order_service.py" in result.diff_text
        assert result.test_passed is True
        assert result.applied_paths == ("src/order_service.py",)
        # original project file remains unchanged
        original = (DEMO_SRC / "src" / "order_service.py").read_text(encoding="utf-8")
        assert "# patched" not in original
    finally:
        worktree.cleanup(result)


def test_isolated_worktree_rejects_disallowed_test_binary() -> None:
    policy = EngineeringPolicy(project_root=DEMO_SRC, allowed_tests=("pytest",))
    worktree = IsolatedWorktree(policy)
    plan = PatchPlan(
        title="bad-test",
        rationale="test",
        patches=(
            FilePatch(
                path="src/order_service.py",
                old_text="coupon_id = request.coupon.id",
                new_text="coupon_id = request.coupon.id",
            ),
        ),
        test_commands=("rm -rf /",),
    )
    with pytest.raises(PermissionError, match="not allowed"):
        worktree.validate_patch_plan(plan)
