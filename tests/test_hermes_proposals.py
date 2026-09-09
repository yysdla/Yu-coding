from project_lens.application.hermes_proposals import propose_patch_plan, propose_project_todo, propose_risk_escalation
from project_lens.application.hermes_validation import validate_patch_plan
from project_lens.domain.models import ProjectRef
from project_lens.runtime.patch_plan import FilePatch


def test_patch_proposal_is_draft_only_and_validates_allowlisted_paths() -> None:
    proposal, plan = propose_patch_plan(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        title="Guard coupon null",
        rationale="Add a null guard before dereference.",
        patches=(FilePatch("src/order.py", "coupon.id", "coupon.id if coupon else None"),),
        test_commands=("pytest -q tests/test_order.py",),
    )

    result = validate_patch_plan(plan)
    assert proposal.requires_approval is True
    assert result.ok is True
    assert result.affected_paths == ("src/order.py",)
    assert "does not execute" in result.warnings[0]


def test_validation_rejects_unsafe_paths_and_commands() -> None:
    _proposal, plan = propose_patch_plan(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        title="bad",
        rationale="bad",
        patches=(FilePatch("../deploy.sh", "a", "b"),),
        test_commands=("git push origin main",),
    )

    result = validate_patch_plan(plan)
    assert result.ok is False
    assert result.errors


def test_risk_escalation_is_an_approval_required_draft() -> None:
    proposal = propose_risk_escalation(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        title="CI failure",
        description="Escalate to owner",
    )
    assert proposal.kind == "risk_escalation"
    assert proposal.requires_approval is True


def test_project_todo_is_an_approval_required_draft() -> None:
    proposal = propose_project_todo(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        title="Document retry policy",
        description="Add the retry contract to the runbook.",
        owner_ids=("u2", "u1"),
    )
    assert proposal.kind == "project_todo"
    assert proposal.requires_approval is True
    assert "u1" in proposal.description
