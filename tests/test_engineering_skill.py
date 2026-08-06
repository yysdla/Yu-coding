from pathlib import Path

import pytest

from project_lens.domain.models import ProjectAnswer, ProjectRef
from project_lens.runtime.patch_plan import FilePatch, PatchPlan
from project_lens.workflow.engineering_bridge import maybe_attach_engineering_proposal
from project_lens.workflow.engineering_skill import ProjectEngineeringSkill, build_null_guard_plan
from project_lens.workflow.skills import ProjectSkill
from tests.context_helpers import ACCESS_SCOPE, build_demo_engine

DEMO_SRC = Path(__file__).parents[1] / "examples" / "payment_service"


def test_engineering_skill_explain_propose_validate_without_apply() -> None:
    skill = ProjectEngineeringSkill.for_project_root(DEMO_SRC)
    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    traceback = (
        'File "src/order_service.py", line 16, in create_order\n'
        "AttributeError: 'NoneType' object has no attribute 'id'"
    )
    proposal = skill.propose_from_traceback(
        project=project,
        traceback=traceback,
        relative_path="src/order_service.py",
        test_commands=('python -c "print(\'ok\')"',),
    )
    assert "Explain:" in proposal.explanation
    assert proposal.validation is not None
    assert proposal.validation.diff_text
    assert proposal.action.tool_name == "engineering_proposal"
    assert proposal.action.arguments["affected_paths"] == ["src/order_service.py"]
    assert "diff_summary" in proposal.action.arguments
    assert proposal.action.arguments["test_passed"] is True
    assert proposal.action.arguments["can_apply"] is False
    assert proposal.can_apply is False
    assert proposal.action.requires_approval is True
    with pytest.raises(PermissionError, match="approval|disabled"):
        skill.apply(proposal.plan)


def test_failed_tests_block_apply_recommendation_payload() -> None:
    skill = ProjectEngineeringSkill.for_project_root(DEMO_SRC)
    project = ProjectRef(tenant_id="demo", project_id="payment")
    plan = PatchPlan(
        title="broken",
        rationale="force failure",
        patches=(
            FilePatch(
                path="src/order_service.py",
                old_text="coupon_id = request.coupon.id",
                new_text="coupon_id = request.coupon.id",
            ),
        ),
        test_commands=('python -c "raise SystemExit(1)"',),
    )
    proposal = skill.propose_plan(project=project, plan=plan)
    assert proposal.validation is not None
    assert proposal.validation.test_passed is False
    assert proposal.action.arguments.get("test_passed") is False
    assert proposal.action.arguments.get("can_apply") is False
    assert "失败" in proposal.action.title or "failed" in proposal.action.title.lower()


def test_build_null_guard_plan_targets_allowed_path() -> None:
    plan = build_null_guard_plan(
        "src/order_service.py",
        old_line="coupon_id = request.coupon.id",
    )
    assert plan.affected_paths() == ("src/order_service.py",)


def test_incident_traceback_attaches_engineering_proposal_via_bridge() -> None:
    engine, _index, project = build_demo_engine(DEMO_SRC)
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "order_service.py", line 16, in create_order\n'
        "AttributeError: 'NoneType' object has no attribute 'id'"
    )
    from project_lens.context.models import AccessContext, ContextQuery

    bundle = engine.search(
        ContextQuery(text=traceback, project=project, limit=10),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({ACCESS_SCOPE}),
        ),
    )
    answer = ProjectAnswer(
        project=project,
        skill=ProjectSkill.INCIDENT_DIAGNOSIS.value,
        status="investigating",
        business_summary="checkout failed",
        technical_summary="null coupon",
        evidence=bundle.evidence,
    )
    skill = ProjectEngineeringSkill.for_project_root(DEMO_SRC)
    updated = maybe_attach_engineering_proposal(
        answer,
        question=traceback,
        skill=ProjectSkill.INCIDENT_DIAGNOSIS,
        evidence=bundle.evidence,
        engineering_skill=skill,
    )
    eng = next(
        action
        for action in updated.recommended_actions
        if action.tool_name == "engineering_proposal"
    )
    assert eng.requires_approval is True
    assert eng.arguments["can_apply"] is False
    assert eng.arguments["test_passed"] is True
    assert "src/order_service.py" in eng.arguments["affected_paths"]
