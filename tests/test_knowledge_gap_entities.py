"""Phase 6: entity-aware KnowledgeGapReport coverage."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from project_lens.context.knowledge_gaps import build_knowledge_gap_report
from project_lens.domain.models import (
    ActionProposal,
    AgentRun,
    Evidence,
    EvidenceType,
    KnowledgeGapType,
    ProjectAnswer,
    ProjectRef,
    RunStatus,
    SourceRef,
)
from project_lens.integrations.feishu.cards import render_answer_card


def _project(*, service: str = "order-service") -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service=service,
        environment="production",
    )


def _evidence(
    *,
    evidence_type: EvidenceType,
    content: str,
    metadata: dict | None = None,
    source_id: str | None = None,
    service: str = "order-service",
) -> Evidence:
    return Evidence(
        type=evidence_type,
        project=_project(service=service),
        source=SourceRef(
            system="test",
            source_id=source_id or f"{evidence_type.value}-{content[:12]}",
        ),
        content=content,
        observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef99",
        metadata=metadata or {},
    )


def test_service_without_owner_emits_targeted_owner_gap() -> None:
    code = _evidence(
        evidence_type=EvidenceType.CODE,
        content="def create_order(): ...",
        source_id="src/order_service.py",
        metadata={"file": "src/order_service.py", "service": "order-service"},
    )
    report = build_knowledge_gap_report(
        (
            code,
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="architecture overview without owners",
            ),
        ),
        project=_project(),
    )
    owner_gaps = [gap for gap in report.gaps if gap.type == KnowledgeGapType.OWNER]
    assert any(gap.target_ref == "service:order-service" for gap in owner_gaps)
    assert any("order-service" in gap.title for gap in owner_gaps)
    service_gap = next(gap for gap in owner_gaps if gap.target_ref == "service:order-service")
    assert code.id in service_gap.evidence_ids
    assert service_gap.source_signal.startswith(
        ("missing_service_owner:", "graph_missing_relation:responsible_for:")
    )


def test_module_without_architecture_doc_is_reported() -> None:
    report = build_knowledge_gap_report(
        (
            _evidence(
                evidence_type=EvidenceType.CODE,
                content="class BillingGateway",
                source_id="src/billing_gateway.py",
                metadata={"file": "src/billing_gateway.py", "service": "billing-service"},
                service="billing-service",
            ),
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="Payment architecture covers order service only.",
                metadata={"owner_user_id": "ou_ada", "service": "order-service"},
            ),
            _evidence(
                evidence_type=EvidenceType.COMMIT,
                content="touch src/billing_gateway.py",
                metadata={"files_changed": ["src/billing_gateway.py"], "branch": "main"},
            ),
            _evidence(
                evidence_type=EvidenceType.TASK,
                content="task",
                metadata={"kind": "task", "assignee": "Ada", "service": "order-service"},
            ),
            _evidence(
                evidence_type=EvidenceType.TASK,
                content="release notes",
                metadata={"kind": "release", "version": "1.0", "service": "order-service"},
            ),
            _evidence(
                evidence_type=EvidenceType.INCIDENT,
                content="root_cause: x\nresolution: y",
                metadata={"incident_id": "INC-1", "root_cause": "x", "resolution": "y"},
            ),
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="decision: keep optional coupon",
            ),
        ),
        project=_project(service="billing-service"),
    )
    arch_gaps = [
        gap
        for gap in report.gaps
        if gap.type == KnowledgeGapType.ARCHITECTURE
        and gap.target_ref == "module:src/billing_gateway.py"
    ]
    assert arch_gaps
    assert "Ada" in arch_gaps[0].suggested_owners or "ou_ada" in arch_gaps[0].suggested_owners
    assert arch_gaps[0].evidence_ids
    assert arch_gaps[0].source_signal.startswith(
        ("missing_module_architecture:", "graph_missing_relation:document_describes_module:")
    )


def test_missing_api_and_release_signals() -> None:
    report = build_knowledge_gap_report(
        (
            _evidence(
                evidence_type=EvidenceType.CODE,
                content="def create_order(): ...",
                source_id="src/order_service.py",
                metadata={"owner": "Ada", "service": "order-service"},
            ),
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="decision: keep optional coupon for order_service.py",
                metadata={"owner_user_id": "ou_ada", "service": "order-service"},
            ),
            _evidence(
                evidence_type=EvidenceType.COMMIT,
                content="commit",
                metadata={"branch": "main", "files_changed": ["src/order_service.py"]},
            ),
            _evidence(
                evidence_type=EvidenceType.TASK,
                content="task",
                metadata={"kind": "task", "assignee": "Ada"},
            ),
            _evidence(
                evidence_type=EvidenceType.INCIDENT,
                content="root_cause: x\nresolution: y",
                metadata={"root_cause": "x", "resolution": "y"},
            ),
        ),
        project=_project(),
    )
    types = {gap.type for gap in report.gaps}
    assert KnowledgeGapType.API_DOC in types
    assert KnowledgeGapType.RELEASE_HISTORY in types
    api_gap = next(gap for gap in report.gaps if gap.type == KnowledgeGapType.API_DOC)
    assert "Ada" in api_gap.suggested_owners or "ou_ada" in api_gap.suggested_owners


def test_feishu_gap_card_shows_target_and_suggested_owner() -> None:
    project = _project()
    evidence_id = uuid4()
    answer = ProjectAnswer(
        project=project,
        skill="project_knowledge",
        confidence=0.6,
        status="identified",
        business_summary="知识库有缺口",
        technical_summary="entity-aware gaps",
        claims=(),
        evidence=(),
        unknowns=(
            "[api_doc|medium] target=project:api_docs | 缺少接口说明：未找到 OpenAPI | "
            f"建议谁补：Ada | 为何判断：missing_project_signal:api_doc | 证据：{evidence_id}",
        ),
        recommended_actions=(
            ActionProposal(
                title="补充：缺少接口说明",
                description="补充接口说明；建议谁补：Ada",
                tool_name="knowledge_gap_action",
                requires_approval=False,
                arguments={},
            ),
        ),
    )
    run = AgentRun(
        id=uuid4(),
        trace_id=uuid4(),
        project=project,
        user_id="u1",
        question="知识库缺什么",
        status=RunStatus.COMPLETED,
    )
    card_text = str(render_answer_card(run, answer))
    assert "**当前未知**" in card_text or "**一句话结论**" in card_text
    assert "target=project:api_docs" in card_text
    assert "建议谁补：Ada" in card_text
    assert "为何判断：missing_project_signal:api_doc" in card_text
    assert f"证据：{evidence_id}" in card_text
    assert "补充：缺少接口说明" in card_text
    assert "来源摘要" in card_text or "当前资料不足" in card_text


def test_specialist_gap_lines_include_signal_and_evidence() -> None:
    from project_lens.workflow.analysis import AnalysisAgent
    from project_lens.context.models import AccessContext, ContextQuery
    from tests.context_helpers import ACCESS_SCOPE, build_demo_engine
    from pathlib import Path

    engine, _index, project = build_demo_engine(
        Path(__file__).parents[1] / "examples" / "payment_service",
        include_git_and_feishu=True,
    )
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE}),
    )
    gaps = engine.knowledge_gaps(project, access)
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
        query=ContextQuery(text="知识库缺什么", project=project, limit=50),
        hits=hits,
    )
    result = AnalysisAgent().analyze(
        "知识库缺什么",
        bundle,
        knowledge_gaps=gaps,
    )
    assert any("为何判断：" in item for item in result.unknowns)
    assert any("证据：" in item for item in result.unknowns)
    assert any(gap.evidence_ids for gap in gaps.gaps)
    assert gaps.signal_coverage
    assert any("主题覆盖：" in claim.text for claim in result.candidates)
