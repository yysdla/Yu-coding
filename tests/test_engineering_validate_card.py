"""Phase 5: Engineering Validate Feishu card shows full read-only checklist."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from project_lens.domain.models import (
    ActionProposal,
    AgentRun,
    Claim,
    ClaimType,
    Evidence,
    EvidenceType,
    ProjectAnswer,
    ProjectRef,
    RunStatus,
    SourceRef,
)
from project_lens.integrations.feishu.audiences import AnswerAudience
from project_lens.integrations.feishu.cards import render_answer_card
from project_lens.runtime.patch_plan import FilePatch, PatchPlan
from project_lens.workflow.engineering_skill import (
    ProjectEngineeringSkill,
    to_engineering_action_proposal,
)
from project_lens.workflow.task_scratchpad import scratchpad_from_engineering_action

DEMO_SRC = Path(__file__).parents[1] / "examples" / "payment_service"


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def _run(project: ProjectRef) -> AgentRun:
    return AgentRun(
        id=uuid4(),
        trace_id=uuid4(),
        project=project,
        user_id="user-1",
        question="怎么修？",
        status=RunStatus.COMPLETED,
    )


def _answer_with_engineering(action: ActionProposal) -> ProjectAnswer:
    project = _project()
    evidence = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local", source_id="src/order_service.py"),
        content="coupon_id = request.coupon.id",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdefaa",
    )
    claim = Claim(
        text="optional coupon can be None",
        type=ClaimType.FACT,
        evidence_ids=(evidence.id,),
    )
    return ProjectAnswer(
        project=project,
        skill="incident_diagnosis",
        confidence=0.7,
        status="identified",
        business_summary="下单可能因 coupon 为空失败",
        technical_summary="AttributeError on request.coupon.id",
        claims=(claim,),
        evidence=(evidence,),
        unknowns=(),
        recommended_actions=(action,),
    )


def test_engineering_validate_card_shows_full_checklist() -> None:
    skill = ProjectEngineeringSkill.for_project_root(DEMO_SRC)
    plan = PatchPlan(
        title="Guard optional coupon",
        rationale="Traceback shows NoneType on coupon.id",
        patches=(
            FilePatch(
                path="src/order_service.py",
                old_text="coupon_id = request.coupon.id",
                new_text="coupon_id = request.coupon.id if request.coupon else None",
            ),
        ),
        test_commands=('python -c "print(\'ok\')"',),
    )
    proposal = skill.propose_plan(project=_project(), plan=plan)
    card = render_answer_card(
        _run(_project()),
        _answer_with_engineering(proposal.action),
        audience=AnswerAudience.TECHNICAL,
    )
    card_text = str(card)

    assert "**修复提案**" in card_text
    assert "Explain → Propose → Validate" in card_text
    assert "问题解释:" in card_text
    assert "相关 Evidence:" in card_text
    assert "src/order_service.py" in card_text
    assert "affected_paths:" in card_text
    assert "patch plan:" in card_text
    assert "Guard optional coupon" in card_text
    assert "diff 摘要:" in card_text
    assert "test commands:" in card_text
    assert "python -c" in card_text
    assert "test result: passed=" in card_text
    assert "failed attempts:" in card_text
    assert "approval requirement" in card_text
    assert "can_apply=False" in card_text
    assert "allow_apply=False" in card_text
    assert "不会执行 Apply" in card_text
    assert "不创建 PR" in card_text

    # No Apply action button (memory approve/reject are unrelated).
    for element in card.get("elements") or []:
        if not isinstance(element, dict) or element.get("tag") != "action":
            continue
        for button in element.get("actions") or []:
            value = (button or {}).get("value") or {}
            assert value.get("action") != "engineering_apply"
            content = ((button or {}).get("text") or {}).get("content") or ""
            assert "Apply" not in content


def test_action_args_include_patch_plan_and_test_commands() -> None:
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
    proposal = skill.propose_plan(project=_project(), plan=plan)
    assert proposal.validation is not None
    assert proposal.validation.test_passed is True
    action = to_engineering_action_proposal(
        explanation="Explain: optional coupon",
        plan=plan,
        validation=proposal.validation,
    )
    args = action.arguments
    assert args["allow_apply"] is False
    assert args["can_apply"] is False
    assert args["requires_approval"] is True
    assert args["patch_plan"]["title"] == "safe patch"
    assert args["patch_plan"]["steps"][0]["path"] == "src/order_service.py"
    assert "safe patch" in args["patch_plan_text"]
    assert args["test_commands"] == ["python -c \"print('ok')\""]
    assert args["failed_attempts"] == []
    assert args["validate_mode"] == "isolated_worktree"


def test_failed_validation_records_failed_attempts_on_card_and_scratchpad() -> None:
    skill = ProjectEngineeringSkill.for_project_root(DEMO_SRC)
    plan = PatchPlan(
        title="failing validate",
        rationale="forced fail",
        patches=(
            FilePatch(
                path="src/order_service.py",
                old_text="coupon_id = request.coupon.id",
                new_text="coupon_id = request.coupon.id",
            ),
        ),
        test_commands=('python -c "raise SystemExit(1)"',),
    )
    proposal = skill.propose_plan(project=_project(), plan=plan)
    assert proposal.validation is not None
    assert proposal.validation.test_passed is False
    assert proposal.action.arguments["failed_attempts"]
    assert "worktree_validate_failed" in proposal.action.arguments["failed_attempts"][0]

    card_text = str(
        render_answer_card(
            _run(_project()),
            _answer_with_engineering(proposal.action),
            audience=AnswerAudience.TECHNICAL,
        )
    )
    assert "failed attempts:" in card_text
    assert "worktree_validate_failed" in card_text
    assert "test result: passed=False" in card_text

    pad = scratchpad_from_engineering_action(proposal.action)
    assert pad.allow_apply is False
    assert pad.test_commands == ('python -c "raise SystemExit(1)"',)
    assert pad.failed_attempts
    assert "failing validate" in (pad.patch_plan or "")
