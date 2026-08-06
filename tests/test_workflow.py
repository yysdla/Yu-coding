from pathlib import Path

import pytest

from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle, RetrievalHit
from project_lens.domain.models import (
    AgentRun,
    ClaimType,
    Evidence,
    EvidenceGrade,
    EvidenceType,
    RunStatus,
    SourceRef,
    utc_now,
)
from project_lens.workflow.analysis import AnalysisAgent
from project_lens.workflow.models import AnalysisResult, CandidateClaim
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.resolver import ProjectResolver
from project_lens.workflow.verification import VerificationAgent
from project_lens.workflow.skills import ProjectSkill, classify_project_question
from tests.context_helpers import ACCESS_SCOPE, build_demo_engine


TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def test_project_question_router_selects_bounded_skills() -> None:
    assert classify_project_question("最近故障") == ProjectSkill.INCIDENT_DIAGNOSIS
    assert classify_project_question("这个接口报错了") == ProjectSkill.INCIDENT_DIAGNOSIS
    assert classify_project_question("线上异常影响范围是什么") == ProjectSkill.INCIDENT_DIAGNOSIS
    assert classify_project_question("请解释这个项目的架构和服务依赖") == ProjectSkill.ARCHITECTURE
    assert classify_project_question("这个版本上线后改了什么") == ProjectSkill.VERSION_CHANGE
    assert classify_project_question("请解释这个函数的实现") == ProjectSkill.CODE_EXPLANATION
    assert classify_project_question("线上出现了故障，影响范围是什么") == ProjectSkill.INCIDENT_DIAGNOSIS
    assert classify_project_question("这个接口报错了") == ProjectSkill.INCIDENT_DIAGNOSIS
    assert classify_project_question("支付项目的负责人是谁") == ProjectSkill.PROJECT_KNOWLEDGE
    assert classify_project_question("介绍一下这个项目") == ProjectSkill.PROJECT_KNOWLEDGE
    assert classify_project_question("这个项目有哪些核心模块") == ProjectSkill.PROJECT_KNOWLEDGE


def test_traceback_is_routed_to_incident_skill() -> None:
    assert classify_project_question(TRACEBACK) == ProjectSkill.INCIDENT_DIAGNOSIS


@pytest.mark.asyncio
async def test_error_workflow_produces_traceable_dual_audience_answer() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    engine, _, project = build_demo_engine(root)
    workflow = ProjectWorkflow(ProjectResolver((project,)), engine)
    run = AgentRun(project=project, user_id="u1", question=TRACEBACK)
    transitions: list[RunStatus] = []
    payloads: list[dict[str, object]] = []

    async def record(status: RunStatus, payload: dict[str, object]) -> None:
        transitions.append(status)
        payloads.append(payload)

    answer = await workflow.execute(run, record)

    assert transitions == [
        RunStatus.RESOLVING,
        RunStatus.COLLECTING,
        RunStatus.ANALYZING,
        RunStatus.VERIFYING,
    ]
    assert payloads[2]["skill"] == ProjectSkill.INCIDENT_DIAGNOSIS.value
    assert payloads[3]["skill"] == ProjectSkill.INCIDENT_DIAGNOSIS.value
    assert "项目资料" in answer.business_summary
    assert "order_service.py#L14-L20" in answer.technical_summary
    assert "INC-2026-001" in answer.technical_summary
    assert answer.skill == ProjectSkill.INCIDENT_DIAGNOSIS.value
    assert answer.recommended_actions[0].requires_approval is True
    assert any("实时监控" in item for item in answer.unknowns)
    known_ids = {item.id for item in answer.evidence}
    assert known_ids
    assert all(set(claim.evidence_ids) <= known_ids for claim in answer.claims)


def test_exception_without_traceback_marks_code_location_as_inference() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    engine, _, project = build_demo_engine(root)
    bundle = engine.search(
        ContextQuery(
            text="AttributeError: 'NoneType' object has no attribute 'id'",
            project=project,
            limit=5,
        ),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({ACCESS_SCOPE}),
        ),
    )

    result = AnalysisAgent().analyze(bundle.query.text, bundle)
    location_claims = [item for item in result.candidates if "候选代码位置" in item.text]

    assert location_claims
    assert location_claims[0].type == ClaimType.INFERENCE
    assert location_claims[0].grade == EvidenceGrade.C
    assert not any(
        item.type == ClaimType.FACT and item.text.startswith("错误位置可定位到")
        for item in result.candidates
    )
    assert any("未提供 traceback" in item for item in result.unknowns)


def test_unmatched_traceback_does_not_create_exact_location_fact() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    engine, _, project = build_demo_engine(root)
    question = (
        'File "missing_service.py", line 999, in execute\n'
        "AttributeError: 'NoneType' object has no attribute 'id'"
    )
    bundle = engine.search(
        ContextQuery(text=question, project=project, limit=5),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({ACCESS_SCOPE}),
        ),
    )

    result = AnalysisAgent().analyze(question, bundle)

    assert not any(
        item.type == ClaimType.FACT and item.text.startswith("错误位置可定位到")
        for item in result.candidates
    )
    assert any("未匹配到" in item for item in result.unknowns)


def test_verifier_rejects_citation_that_does_not_support_claim() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    engine, _, project = build_demo_engine(root)
    bundle = engine.search(
        ContextQuery(text=TRACEBACK, project=project, limit=5),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({ACCESS_SCOPE}),
        ),
    )
    candidate = CandidateClaim(
        text="数据库连接池已经耗尽。",
        type=ClaimType.FACT,
        grade=EvidenceGrade.A,
        evidence_ids=(bundle.evidence[0].id,),
        support_terms=("database connection pool exhausted",),
    )

    result = VerificationAgent().verify(
        AnalysisResult(problem_kind="error", candidates=(candidate,)),
        bundle,
        project,
    )

    assert result.claims == ()
    assert result.issues[0].reason == "claim is not supported by the cited evidence content"


@pytest.mark.parametrize(
    ("question", "skill", "expected_text"),
    (
        ("请解释这个项目的架构和服务依赖", ProjectSkill.ARCHITECTURE, "项目架构资料"),
        ("请解释 create_order 这个函数的实现", ProjectSkill.CODE_EXPLANATION, "代码实现"),
        ("支付项目的负责人和背景是什么", ProjectSkill.PROJECT_KNOWLEDGE, "项目问题"),
    ),
)
def test_read_only_specialists_produce_verifiable_claims(
    question: str, skill: ProjectSkill, expected_text: str
) -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    engine, _, project = build_demo_engine(root)
    bundle = engine.search(
        ContextQuery(text=question, project=project, limit=5),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({ACCESS_SCOPE}),
        ),
    )

    analysis = AnalysisAgent().analyze(question, bundle, skill=skill)
    verification = VerificationAgent().verify(analysis, bundle, project)

    assert analysis.skill == skill
    assert any(expected_text in item.text for item in analysis.candidates)
    assert verification.claims
    assert not verification.issues


def test_architecture_specialist_adds_project_map_from_snapshot() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    engine, _, project = build_demo_engine(root, include_git_and_feishu=True)
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE}),
    )
    question = "请解释这个项目的架构、服务、入口、依赖和风险，形成项目地图。"
    authorized = engine.authorized_evidence(project, access, limit=50)
    from project_lens.context.models import EvidenceBundle, RetrievalHit

    hits = tuple(
        RetrievalHit(
            evidence=item,
            score=1.0,
            channels=("authorized",),
            channel_ranks={"authorized": index + 1},
        )
        for index, item in enumerate(authorized)
    )
    bundle = EvidenceBundle(
        query=ContextQuery(text=question, project=project, limit=max(len(hits), 1)),
        hits=hits,
    )
    gaps = engine.knowledge_gaps(project, access)

    analysis = AnalysisAgent().analyze(
        question, bundle, skill=ProjectSkill.ARCHITECTURE, knowledge_gaps=gaps
    )
    verification = VerificationAgent().verify(analysis, bundle, project)

    texts = [item.text for item in analysis.candidates]
    assert any(text.startswith("项目地图：") for text in texts)
    assert any(text.startswith("核心服务：") for text in texts)
    assert any(text.startswith("关键入口：") for text in texts)
    assert any(text.startswith("上下游依赖：") for text in texts)
    assert any(claim.text.startswith("项目地图：") for claim in verification.claims)
    assert any("payment service" in claim.text for claim in verification.claims)
    for claim in verification.claims:
        assert claim.evidence_ids


def test_version_change_specialist_uses_release_evidence() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    _, _, project = build_demo_engine(root)
    evidence = Evidence(
        type=EvidenceType.COMMIT,
        project=project,
        source=SourceRef(system="git", source_id="commit:abc123"),
        content="release abc123 changed coupon null guard and checkout validation",
        observed_at=utc_now(),
        access_scope=ACCESS_SCOPE,
        content_hash="release-change-abc123",
    )
    bundle = EvidenceBundle(
        query=ContextQuery(text="这个版本上线后改了什么", project=project),
        hits=(
            RetrievalHit(
                evidence=evidence,
                score=1.0,
                channels=("fixture",),
                channel_ranks={"fixture": 1},
            ),
        ),
    )

    analysis = AnalysisAgent().analyze(
        bundle.query.text, bundle, skill=ProjectSkill.VERSION_CHANGE
    )
    verification = VerificationAgent().verify(analysis, bundle, project)

    assert analysis.skill == ProjectSkill.VERSION_CHANGE
    assert any(item.text.startswith("项目时间线显示：") for item in analysis.candidates)
    assert any("版本或变更" in item.text for item in analysis.candidates)
    assert any(item.title == "按时间线核对版本变更" for item in analysis.actions)
    assert verification.claims
    assert verification.actions


def test_version_change_specialist_adds_likely_impact_from_timeline_and_incident() -> None:
    root = Path(__file__).parents[1] / "examples" / "payment_service"
    _, _, project = build_demo_engine(root)
    commit = Evidence(
        type=EvidenceType.COMMIT,
        project=project,
        source=SourceRef(system="git", source_id="commit:abc123"),
        content="release abc123 changed coupon null guard and checkout validation",
        observed_at=utc_now(),
        access_scope=ACCESS_SCOPE,
        content_hash="release-change-abc123",
    )
    incident = Evidence(
        type=EvidenceType.INCIDENT,
        project=project,
        source=SourceRef(system="feishu", source_id="INC-2026-001"),
        content=(
            "Orders without coupons failed during checkout.\n"
            "The coupon null guard was removed during a refactor.\n"
            "Checkout failed for customers who did not select a coupon."
        ),
        observed_at=utc_now(),
        access_scope=ACCESS_SCOPE,
        content_hash="incident-coupon-null-guard",
    )
    bundle = EvidenceBundle(
        query=ContextQuery(text="这个版本上线后影响了什么", project=project, limit=5),
        hits=(
            RetrievalHit(
                evidence=commit,
                score=1.0,
                channels=("fixture",),
                channel_ranks={"fixture": 1},
            ),
            RetrievalHit(
                evidence=incident,
                score=0.9,
                channels=("fixture",),
                channel_ranks={"fixture": 2},
            ),
        ),
    )

    analysis = AnalysisAgent().analyze(
        bundle.query.text, bundle, skill=ProjectSkill.VERSION_CHANGE
    )
    verification = VerificationAgent().verify(analysis, bundle, project)

    assert analysis.skill == ProjectSkill.VERSION_CHANGE
    assert any("影响" in item.text for item in analysis.candidates)
    assert any(item.title == "优先回看 checkout / coupon 回归" for item in analysis.actions)
    assert any("coupon null guard regression" in claim.text for claim in verification.claims)
    assert verification.claims
    assert verification.actions
