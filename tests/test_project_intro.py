"""Project introduction skill: routing, analysis, and evidence-backed claims."""

from pathlib import Path

from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle, RetrievalHit
from project_lens.domain.models import Evidence, ProjectRef
from project_lens.workflow.analysis import AnalysisAgent
from project_lens.workflow.skills import (
    ProjectSkill,
    classify_project_question,
    is_project_intro_question,
)
from tests.context_helpers import ACCESS_SCOPE, build_demo_engine

DEMO_SRC = Path(__file__).parents[1] / "examples" / "payment_service"


def test_is_project_intro_question_detects_common_asks() -> None:
    assert is_project_intro_question("介绍一下这个项目")
    assert is_project_intro_question("这个项目是做什么的")
    assert is_project_intro_question("这个项目有哪些核心模块")
    assert is_project_intro_question(
        "请介绍一下这个项目：项目定位、核心服务、关键入口、主要文档、"
        "负责人、最近变更、风险和知识缺口。"
    )
    assert not is_project_intro_question("项目地图")
    assert not is_project_intro_question("知识库缺什么")
    assert not is_project_intro_question("负责人是谁")


def test_intro_questions_route_to_project_knowledge_not_architecture() -> None:
    assert classify_project_question("介绍一下这个项目") == ProjectSkill.PROJECT_KNOWLEDGE
    assert classify_project_question("这个项目有哪些核心模块") == ProjectSkill.PROJECT_KNOWLEDGE
    assert classify_project_question("这个项目是做什么的") == ProjectSkill.PROJECT_KNOWLEDGE
    # Architecture map phrasing stays on architecture skill.
    assert classify_project_question("请解释这个项目的架构和服务依赖") == ProjectSkill.ARCHITECTURE


def test_project_intro_analysis_covers_required_sections() -> None:
    engine, _index, project = build_demo_engine(
        DEMO_SRC,
        include_git_and_feishu=True,
    )
    question = (
        "请介绍一下这个项目：项目定位、核心服务、关键入口、主要文档、"
        "负责人、最近变更、风险和知识缺口。"
    )
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE}),
    )
    authorized = engine.authorized_evidence(project, access, limit=50)
    bundle = _bundle_from_authorized(authorized, project=project, question=question)
    gaps = engine.knowledge_gaps(project, access)
    result = AnalysisAgent().analyze(
        question,
        bundle,
        knowledge_gaps=gaps,
    )

    assert result.skill == ProjectSkill.PROJECT_KNOWLEDGE
    texts = [item.text for item in result.candidates]
    assert any(text.startswith("项目定位：") for text in texts)
    assert any(text.startswith("核心服务：") for text in texts)
    assert any(text.startswith("关键入口：") for text in texts)
    assert any(text.startswith("主要文档：") for text in texts)
    assert any("负责人" in text for text in texts)
    assert any(text.startswith("最近变更：") for text in texts)
    has_risk_or_gap = any(text.startswith("风险信号：") for text in texts) or any(
        item.startswith("知识缺口") or item.startswith("[") for item in result.unknowns
    )
    assert has_risk_or_gap
    for claim in result.candidates:
        assert claim.evidence_ids
        assert claim.support_terms


def _bundle_from_authorized(
    evidence: tuple[Evidence, ...],
    *,
    project: ProjectRef,
    question: str,
) -> EvidenceBundle:
    hits = tuple(
        RetrievalHit(
            evidence=item,
            score=1.0,
            channels=("authorized",),
            channel_ranks={"authorized": index + 1},
        )
        for index, item in enumerate(evidence)
    )
    return EvidenceBundle(
        query=ContextQuery(text=question, project=project, limit=max(len(hits), 1)),
        hits=hits,
        retrieval_trace={
            "authorized_candidate_count": len(hits),
            "channel_counts": {"authorized": len(hits)},
        },
    )
