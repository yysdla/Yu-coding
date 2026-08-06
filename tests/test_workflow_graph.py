from pathlib import Path

from project_lens.context.models import AccessContext, ContextQuery
from project_lens.graph.models import GraphNodeKind
from project_lens.workflow.analysis import AnalysisAgent
from project_lens.workflow.graph_hints import graph_queries_for_question
from project_lens.workflow.skills import ProjectSkill
from tests.context_helpers import ACCESS_SCOPE, build_demo_engine

DEMO = Path(__file__).parents[1] / "examples" / "payment_service"


def test_graph_queries_are_skill_scoped() -> None:
    owner_queries = graph_queries_for_question(
        ProjectSkill.PROJECT_KNOWLEDGE,
        "负责人是谁",
    )
    architecture_queries = graph_queries_for_question(
        ProjectSkill.ARCHITECTURE,
        "这个服务上下游是谁？",
    )
    incident_queries = graph_queries_for_question(
        ProjectSkill.INCIDENT_DIAGNOSIS,
        "最近故障",
    )
    version_queries = graph_queries_for_question(
        ProjectSkill.VERSION_CHANGE,
        "最近变更",
    )
    code_queries = graph_queries_for_question(
        ProjectSkill.CODE_EXPLANATION,
        "create_order 怎么实现",
    )

    assert any(query.start_kind == GraphNodeKind.OWNER for query in owner_queries)
    assert any(
        query.relation == "depends_on" and query.start_kind == GraphNodeKind.SERVICE
        for query in architecture_queries
    )
    assert any(
        query.relation == "exposes" and query.end_kind == GraphNodeKind.ENDPOINT
        for query in architecture_queries
    )
    assert any(
        query.start_kind == GraphNodeKind.DOCUMENT and query.end_kind == GraphNodeKind.MODULE
        for query in architecture_queries
    )
    assert any(query.start_kind == GraphNodeKind.INCIDENT for query in incident_queries)
    assert any(
        query.relation == "exposes" and query.end_kind == GraphNodeKind.ENDPOINT
        for query in incident_queries
    )
    assert any(
        query.relation == "implemented_by"
        and query.start_kind == GraphNodeKind.ENDPOINT
        and query.end_kind == GraphNodeKind.MODULE
        for query in incident_queries
    )
    assert any(
        query.relation == "implemented_by"
        and query.start_kind == GraphNodeKind.ENDPOINT
        and query.end_kind == GraphNodeKind.SYMBOL
        for query in incident_queries
    )
    assert any(query.start_kind == GraphNodeKind.RELEASE for query in version_queries)
    assert code_queries == ()


def test_analysis_emits_verifiable_graph_relation_claims() -> None:
    engine, _index, project = build_demo_engine(DEMO, include_git_and_feishu=True)
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE}),
    )
    question = "这个项目的负责人是谁？如果证据不足，请说明缺少哪些负责人资料。"
    bundle = engine.search(
        ContextQuery(text=question, project=project, limit=12),
        access,
    )
    paths = ()
    for query in graph_queries_for_question(ProjectSkill.PROJECT_KNOWLEDGE, question):
        paths = paths + engine.query_graph(project, access, query)
    assert paths

    result = AnalysisAgent().analyze(
        question,
        bundle,
        skill=ProjectSkill.PROJECT_KNOWLEDGE,
        graph_paths=paths[:5],
    )
    assert any("项目关系图显示" in claim.text for claim in result.candidates)
    graph_claim = next(
        claim for claim in result.candidates if "项目关系图显示" in claim.text
    )
    assert graph_claim.evidence_ids
    assert graph_claim.support_terms


def test_architecture_upstream_question_emits_depends_on_graph_claim() -> None:
    engine, _index, project = build_demo_engine(DEMO, include_git_and_feishu=True)
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE}),
    )
    question = "这个服务上下游是谁？"
    bundle = engine.search(
        ContextQuery(text=question, project=project, limit=12),
        access,
    )
    paths = ()
    for query in graph_queries_for_question(ProjectSkill.ARCHITECTURE, question):
        paths = paths + engine.query_graph(project, access, query)
    assert any(
        "depends_on" in item.relations or "payment-service" in item.path_labels
        for item in paths
    )
    result = AnalysisAgent().analyze(
        question,
        bundle,
        skill=ProjectSkill.ARCHITECTURE,
        graph_paths=paths[:5],
    )
    assert any("项目关系图显示" in claim.text for claim in result.candidates)
