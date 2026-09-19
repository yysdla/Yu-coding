from __future__ import annotations

from datetime import datetime, timezone

from project_lens.application.unified_knowledge_retrieval import (
    SourceLayer,
    UnifiedKnowledgeRetrievalService,
)
from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle, RetrievalHit
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef


class FakeContext:
    def __init__(self, evidence: Evidence) -> None:
        self.evidence = evidence

    def search(self, query: ContextQuery, access: AccessContext) -> EvidenceBundle:
        return EvidenceBundle(
            query=query,
            hits=(
                RetrievalHit(
                    evidence=self.evidence,
                    score=0.4,
                    channels=("bm25",),
                    channel_ranks={"bm25": 1},
                ),
            ),
        )


class FakeWiki:
    def search(self, **kwargs):
        from project_lens.obsidian.repository import WikiPageSummary

        return (
            WikiPageSummary(
                path="20-wiki/overview.md",
                title="派生摘要",
                page_type="overview",
                status="draft",
                snippet="这是 Wiki 摘要",
                source_keys=("t1:p1:req:1",),
                generated_at=None,
            ),
        )


def test_unified_results_preserve_source_layer_and_prioritize_evidence() -> None:
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=ProjectRef(tenant_id="t1", project_id="p1"),
        source=SourceRef(system="docs", source_id="req-1"),
        content="正式需求",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:p1:read",
        content_hash="a" * 64,
    )
    service = UnifiedKnowledgeRetrievalService(
        context_engine=FakeContext(evidence),
        wiki_repository=FakeWiki(),
    )
    results = service.search(
        project=evidence.project,
        access=AccessContext(
            tenant_id="t1",
            user_id="u1",
            permissions=frozenset({"project:p1:read"}),
        ),
        query="需求",
        include_derived=True,
    )

    assert results[0].source_layer is SourceLayer.EVIDENCE
    assert results[0].derived is False
    assert results[1].source_layer is SourceLayer.OBSIDIAN_WIKI
    assert results[1].derived is True


def test_wiki_results_are_marked_derived_and_not_detail_authority() -> None:
    evidence = Evidence(
        type=EvidenceType.DOCUMENT,
        project=ProjectRef(tenant_id="t1", project_id="p1"),
        source=SourceRef(system="docs", source_id="req-1"),
        content="正式需求",
        observed_at=datetime.now(timezone.utc),
        access_scope="project:p1:read",
        content_hash="a" * 64,
    )
    service = UnifiedKnowledgeRetrievalService(
        context_engine=FakeContext(evidence),
        wiki_repository=FakeWiki(),
    )
    wiki = service.search_wiki(project=evidence.project, query="需求")

    assert wiki[0].derived is True
    assert wiki[0].source_layer is SourceLayer.OBSIDIAN_WIKI
    assert wiki[0].provenance["source_keys"] == ["t1:p1:req:1"]
