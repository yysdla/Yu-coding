from project_lens.context.models import AccessContext, ContextQuery
from project_lens.workflow.analysis import AnalysisAgent
from project_lens.workflow.skills import ProjectSkill, is_owner_lookup_question
from tests.context_helpers import ACCESS_SCOPE, build_demo_engine
from pathlib import Path

DEMO_SRC = Path(__file__).parents[1] / "examples" / "payment_service"


def test_is_owner_lookup_question_detects_shortcuts_and_rewrites() -> None:
    assert is_owner_lookup_question("负责人是谁")
    assert is_owner_lookup_question("谁负责 order-service")
    assert is_owner_lookup_question(
        "这个项目的负责人是谁？如果证据不足，请说明缺少哪些负责人资料。"
    )
    assert not is_owner_lookup_question("项目地图")
    assert not is_owner_lookup_question("知识库缺什么")


def test_owner_lookup_analysis_returns_ada_from_feishu_docs() -> None:
    engine, _index, project = build_demo_engine(
        DEMO_SRC,
        include_git_and_feishu=True,
    )
    question = "这个项目的负责人是谁？如果证据不足，请说明缺少哪些负责人资料。"
    bundle = engine.search(
        ContextQuery(text=question, project=project, limit=12),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({ACCESS_SCOPE}),
        ),
    )
    result = AnalysisAgent().analyze(question, bundle)
    assert result.skill == ProjectSkill.PROJECT_KNOWLEDGE
    assert any("Ada" in claim.text for claim in result.candidates)
    assert not any("traceback" in item.lower() for item in result.unknowns)
