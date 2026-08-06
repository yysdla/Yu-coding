"""Authorized multi-channel project evidence retrieval."""

from __future__ import annotations

from project_lens.context.access import EvidenceAccessPolicy
from project_lens.context.change_impact import build_change_impact
from project_lens.context.graph_service import ContextGraphService
from project_lens.context.knowledge_gaps import build_knowledge_gap_report
from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle, TimeRange
from project_lens.context.ops.query import OpsQueryService
from project_lens.context.ops.store import InMemoryOpsSignalStore
from project_lens.context.retrieval.bm25 import BM25Retriever
from project_lens.context.retrieval.exact import ExactCodeRetriever, parse_traceback
from project_lens.context.retrieval.fusion import ReciprocalRankFusion
from project_lens.context.snapshot import build_project_snapshot
from project_lens.context.store import EvidenceIndex
from project_lens.context.timeline import build_timeline_events
from project_lens.domain.models import (
    ChangeImpact,
    Evidence,
    GraphEvidence,
    KnowledgeGapReport,
    ProjectRef,
    ProjectSnapshot,
    TimelineEvent,
)
from project_lens.domain.ops import OpsFinding, OpsQuery
from project_lens.graph.builder import EvidenceGraphBuilder
from project_lens.graph.query import GraphQuery


class ContextEngine:
    def __init__(
        self,
        index: EvidenceIndex,
        *,
        access_policy: EvidenceAccessPolicy | None = None,
        exact_retriever: ExactCodeRetriever | None = None,
        bm25_retriever: BM25Retriever | None = None,
        fusion: ReciprocalRankFusion | None = None,
        graph_service: ContextGraphService | None = None,
        ops_service: OpsQueryService | None = None,
    ) -> None:
        self._index = index
        self._access = access_policy or EvidenceAccessPolicy()
        self._exact = exact_retriever or ExactCodeRetriever()
        self._bm25 = bm25_retriever or BM25Retriever()
        self._fusion = fusion or ReciprocalRankFusion()
        self._graph = graph_service or ContextGraphService()
        self._ops = ops_service or OpsQueryService()

    def search(self, query: ContextQuery, access: AccessContext) -> EvidenceBundle:
        all_evidence = self._index.all()
        candidates = self._access.filter(all_evidence, query, access)
        channel_limit = max(query.limit * 3, query.limit)
        search_text = _expand_query_text(query.text)
        exact_hits = self._exact.retrieve(search_text, candidates, limit=channel_limit)
        bm25_hits = self._bm25.retrieve(search_text, candidates, limit=channel_limit)
        hits = self._fusion.fuse(
            {"exact": exact_hits, "bm25": bm25_hits},
            weights={"exact": 3.0, "bm25": 1.0},
            limit=query.limit,
        )
        frames, exception = parse_traceback(query.text)
        warnings: list[str] = []
        if frames and not exact_hits:
            warnings.append("traceback source location did not match authorized indexed code")
        if not hits:
            warnings.append("no authorized project evidence matched the query")
        return EvidenceBundle(
            query=query,
            hits=tuple(hits),
            retrieval_trace={
                "indexed_count": len(all_evidence),
                "authorized_candidate_count": len(candidates),
                "channel_counts": {"exact": len(exact_hits), "bm25": len(bm25_hits)},
                "search_text": search_text,
                "traceback_frames": len(frames),
                "exception": exception,
            },
            warnings=tuple(warnings),
        )

    def snapshot(self, project: ProjectRef, access: AccessContext) -> ProjectSnapshot:
        query = ContextQuery(text="project snapshot", project=project, limit=50)
        all_evidence = self._index.all()
        candidates = self._access.filter(all_evidence, query, access)
        return build_project_snapshot(candidates, project=project)

    def timeline(
        self,
        project: ProjectRef,
        access: AccessContext,
        *,
        time_range: TimeRange | None = None,
        limit: int = 20,
    ) -> tuple[TimelineEvent, ...]:
        query = ContextQuery(
            text="project timeline",
            project=project,
            time_range=time_range,
            limit=min(max(limit, 1), 50),
        )
        all_evidence = self._index.all()
        candidates = self._access.filter(all_evidence, query, access)
        return build_timeline_events(candidates, project=project)[: query.limit]

    def change_impact(
        self,
        project: ProjectRef,
        access: AccessContext,
        *,
        time_range: TimeRange | None = None,
        limit: int = 20,
    ) -> ChangeImpact:
        query = ContextQuery(
            text="project change impact",
            project=project,
            time_range=time_range,
            limit=min(max(limit, 1), 50),
        )
        all_evidence = self._index.all()
        candidates = self._access.filter(all_evidence, query, access)
        timeline = build_timeline_events(candidates, project=project)[: query.limit]
        snapshot = build_project_snapshot(candidates, project=project)
        return build_change_impact(
            candidates,
            project=project,
            snapshot=snapshot,
            timeline=timeline,
        )

    def authorized_evidence(
        self,
        project: ProjectRef,
        access: AccessContext,
        *,
        limit: int = 50,
    ) -> tuple[Evidence, ...]:
        query = ContextQuery(
            text="authorized project evidence",
            project=project,
            limit=min(max(limit, 1), 50),
        )
        return self._access.filter(self._index.all(), query, access)[: query.limit]

    def knowledge_gaps(self, project: ProjectRef, access: AccessContext) -> KnowledgeGapReport:
        candidates = self.authorized_evidence(project, access, limit=50)
        snapshot = build_project_snapshot(candidates, project=project)
        graph = EvidenceGraphBuilder().build(candidates)
        return build_knowledge_gap_report(
            candidates,
            project=project,
            snapshot=snapshot,
            graph=graph,
        )

    def query_graph(
        self,
        project: ProjectRef,
        access: AccessContext,
        query: GraphQuery,
    ) -> tuple[GraphEvidence, ...]:
        context_query = ContextQuery(text="project graph query", project=project, limit=50)
        candidates = self._access.filter(self._index.all(), context_query, access)
        return self._graph.query(candidates, project=project, query=query)

    def query_ops(self, query: OpsQuery, access: AccessContext) -> OpsFinding:
        """Query logs/metrics/traces in a time window without indexing them into RAG."""

        return self._ops.query(query, access)

    @property
    def ops_store(self) -> InMemoryOpsSignalStore:
        return self._ops.store


def _expand_query_text(text: str) -> str:
    normalized = text.casefold()
    hints: list[str] = [text]
    mapping = (
        ("架构", "architecture dependency module service relation owner"),
        ("依赖", "architecture dependency module service relation depends_on"),
        ("上下游", "architecture dependency module service relation depends_on upstream downstream"),
        ("上游", "architecture dependency module service relation depends_on upstream"),
        ("下游", "architecture dependency module service relation depends_on downstream"),
        ("版本", "release version commit pull request changed refactor"),
        ("发布", "release version commit pull request changed refactor"),
        ("上线", "release version commit pull request changed refactor"),
        ("变更", "release version commit pull request changed refactor"),
        ("代码", "code function method implementation create_order"),
        ("函数", "code function method implementation create_order"),
        ("方法", "code function method implementation create_order"),
        ("实现", "code function method implementation create_order"),
        ("故障", "error exception traceback incident outage failed failure root_cause resolution impact"),
        ("报错", "error exception traceback incident outage failed failure root_cause resolution impact"),
        ("异常", "error exception traceback incident outage failed failure root_cause resolution impact"),
        ("错误", "error exception traceback incident outage failed failure root_cause resolution impact"),
        ("项目", "architecture runbook incident release owner background"),
        ("介绍", "architecture service entrypoint module owner release document"),
        ("简介", "architecture service entrypoint module owner release document"),
        ("核心模块", "architecture module service entrypoint dependency"),
        ("负责人", "owner team project"),
        ("背景", "architecture runbook incident"),
    )
    for marker, hint in mapping:
        if marker in normalized:
            hints.append(hint)
    return " ".join(dict.fromkeys(part for part in hints if part))
