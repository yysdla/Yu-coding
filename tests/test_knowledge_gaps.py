from datetime import datetime, timezone

from project_lens.context.knowledge_gaps import build_knowledge_gap_report, is_knowledge_gap_question
from project_lens.domain.models import (
    Evidence,
    EvidenceType,
    KnowledgeGapType,
    ProjectRef,
    SourceRef,
)
from project_lens.graph.builder import EvidenceGraphBuilder


def _project() -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id="payment", service="order-service")


def _evidence(
    *,
    evidence_type: EvidenceType,
    content: str,
    metadata: dict | None = None,
) -> Evidence:
    return Evidence(
        type=evidence_type,
        project=_project(),
        source=SourceRef(system="test", source_id=f"{evidence_type.value}-1"),
        content=content,
        observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef99",
        metadata=metadata or {},
    )


def test_is_knowledge_gap_question_detects_shortcuts() -> None:
    assert is_knowledge_gap_question("知识库缺什么")
    assert is_knowledge_gap_question("项目知识库缺什么资料？请列出缺失证据")
    assert not is_knowledge_gap_question("项目架构是什么")


def test_build_report_uses_deterministic_signals_only() -> None:
    report = build_knowledge_gap_report(
        (
            _evidence(evidence_type=EvidenceType.CODE, content="def create_order(): ..."),
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="order service depends on payment service",
            ),
        ),
        project=_project(),
    )
    types = {gap.type for gap in report.gaps}
    signals = {gap.source_signal for gap in report.gaps}
    assert KnowledgeGapType.VERSION_HISTORY in types
    assert KnowledgeGapType.TASK_TRACKING in types
    assert KnowledgeGapType.INCIDENT_REVIEW in types
    assert KnowledgeGapType.OWNER in types
    assert KnowledgeGapType.DECISION_RECORD in types
    assert "missing_evidence_type:commit" in signals
    assert all(gap.title and gap.description and gap.source_signal for gap in report.gaps)


def test_empty_authorized_evidence_is_access_limited_gap() -> None:
    report = build_knowledge_gap_report((), project=_project())
    assert len(report.gaps) == 1
    assert report.gaps[0].type == KnowledgeGapType.ACCESS_LIMITED
    assert report.gaps[0].evidence_ids == ()
    assert report.gaps[0].source_signal.startswith("access_limited:")


def test_owner_signal_suppresses_owner_gap() -> None:
    report = build_knowledge_gap_report(
        (
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="architecture overview",
                metadata={"owner_user_id": "ou_ada"},
            ),
            _evidence(evidence_type=EvidenceType.CODE, content="create_order"),
            _evidence(evidence_type=EvidenceType.COMMIT, content="commit abc"),
            _evidence(evidence_type=EvidenceType.TASK, content="task-1"),
            _evidence(
                evidence_type=EvidenceType.INCIDENT,
                content="root_cause: x\nresolution: y",
            ),
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="decision: keep optional coupon",
            ),
            _evidence(
                evidence_type=EvidenceType.DOCUMENT,
                content="runbook: restart order-service when checkout fails",
                metadata={"kind": "runbook"},
            ),
        ),
        project=_project(),
    )
    assert KnowledgeGapType.OWNER not in {gap.type for gap in report.gaps}
    assert KnowledgeGapType.RUNBOOK not in {gap.type for gap in report.gaps}


def test_missing_runbook_and_signal_coverage() -> None:
    items = (
        _evidence(
            evidence_type=EvidenceType.CODE,
            content="def create_order(): ...",
            metadata={"owner": "Ada", "service": "order-service"},
        ),
        _evidence(
            evidence_type=EvidenceType.DOCUMENT,
            content="architecture overview and decision: keep coupon",
            metadata={"owner_user_id": "ou_ada"},
        ),
        _evidence(evidence_type=EvidenceType.COMMIT, content="commit", metadata={"branch": "main"}),
        _evidence(evidence_type=EvidenceType.TASK, content="task"),
        _evidence(
            evidence_type=EvidenceType.INCIDENT,
            content="root_cause: x\nresolution: y",
            metadata={"incident_id": "INC-9", "root_cause": "x", "resolution": "y"},
        ),
        _evidence(
            evidence_type=EvidenceType.DOCUMENT,
            content="openapi paths for /checkout",
        ),
        _evidence(
            evidence_type=EvidenceType.TASK,
            content="release 1.2",
            metadata={"kind": "release", "version": "1.2"},
        ),
    )
    report = build_knowledge_gap_report(items, project=_project())
    assert report.signal_coverage.get("runbook") == 0
    assert report.signal_coverage.get("owner") == 1
    assert report.signal_coverage.get("api_doc") == 1
    assert KnowledgeGapType.RUNBOOK in {gap.type for gap in report.gaps}
    runbook = next(gap for gap in report.gaps if gap.type == KnowledgeGapType.RUNBOOK)
    assert runbook.source_signal.startswith("missing_project_signal:runbook")
    assert runbook.target_ref == "project:runbooks"


def test_graph_missing_owner_relation_emits_graph_gap() -> None:
    items = (
        _evidence(
            evidence_type=EvidenceType.CODE,
            content="def create_order(): ...",
            metadata={"file": "src/order_service.py", "service": "order-service"},
        ),
        _evidence(
            evidence_type=EvidenceType.DOCUMENT,
            content="architecture overview without owners",
        ),
    )
    graph = EvidenceGraphBuilder().build(items)
    report = build_knowledge_gap_report(items, project=_project(), graph=graph)
    owner_gaps = [
        gap
        for gap in report.gaps
        if gap.type == KnowledgeGapType.OWNER and gap.target_ref == "service:order-service"
    ]
    assert owner_gaps
    assert owner_gaps[0].source_signal.startswith("graph_missing_relation:responsible_for:")
    assert owner_gaps[0].graph_summaries
    assert "responsible_for" in owner_gaps[0].graph_summaries[0]
