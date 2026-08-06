from pathlib import Path

from project_lens.domain.models import ProjectRef
from project_lens.runtime.patch_plan import FilePatch, PatchPlan
from project_lens.workflow.engineering_skill import ProjectEngineeringSkill

DEMO_SRC = Path(__file__).parents[1] / "examples" / "payment_service"


def test_patch_proposal_includes_diff_when_tests_pass() -> None:
    skill = ProjectEngineeringSkill.for_project_root(DEMO_SRC)
    plan = PatchPlan(
        title="safe patch",
        rationale="validate only",
        patches=(
            FilePatch(
                path="src/order_service.py",
                old_text="coupon_id = request.coupon.id",
                new_text="coupon_id = request.coupon.id",
            ),
        ),
        test_commands=('python -c "print(\'ok\')"',),
    )
    proposal = skill.propose_plan(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        plan=plan,
    )
    assert proposal.validation is not None
    assert proposal.validation.test_passed is True
    assert proposal.action.arguments["test_passed"] is True
    assert "diff_summary" in proposal.action.arguments
    assert proposal.action.arguments["can_apply"] is False
    assert proposal.action.arguments["allow_apply"] is False
    assert proposal.action.arguments["patch_plan"]["title"] == "safe patch"
    assert proposal.action.arguments["test_commands"] == ['python -c "print(\'ok\')"']
    assert proposal.action.arguments["failed_attempts"] == []
    assert proposal.action.requires_approval is True
    assert proposal.can_apply is False
