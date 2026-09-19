"""Authorized multi-channel project evidence retrieval."""

from __future__ import annotations

from datetime import datetime

from project_lens.application.risk_engine import RiskEngine, RiskScanResult
from project_lens.application.risk_store import InMemoryRiskStore
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
from project_lens.context.retrieval.config import RetrievalConfig
from project_lens.context.snapshot import build_project_snapshot
from project_lens.context.store import EvidenceIndex
from project_lens.context.authority import AuthorityResolution, SourceGap, detect_gaps, resolve_fact
from project_lens.context.source_records import FactType, SourceRecord
from project_lens.context.source_store import SourceRecordStore
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
        risk_engine: RiskEngine | None = None,
        source_store: SourceRecordStore | None = None,
        retrieval_config: RetrievalConfig | None = None,
        vector_retriever=None,
    ) -> None:
        self._index = index
        self._access = access_policy or EvidenceAccessPolicy()
        self._exact = exact_retriever or ExactCodeRetriever()
        self._bm25 = bm25_retriever or BM25Retriever()
        self._fusion = fusion or ReciprocalRankFusion()
        self._graph = graph_service or ContextGraphService()
        self._ops = ops_service or OpsQueryService()
        self._risks = risk_engine or RiskEngine(InMemoryRiskStore())
        self._source_store = source_store
        self._retrieval_config = retrieval_config or RetrievalConfig()
        self._vector = vector_retriever

    def search(self, query: ContextQuery, access: AccessContext) -> EvidenceBundle:
        all_evidence = self._index.all()
        candidates = self._access.filter(all_evidence, query, access)
        channel_limit = max(query.limit * 3, query.limit)
        search_text = _expand_query_text(query.text)
        exact_hits = self._exact.retrieve(search_text, candidates, limit=channel_limit)
        bm25_hits = self._bm25.retrieve(search_text, candidates, limit=channel_limit)
        vector_failure: str | None = None
        effective_mode = self._retrieval_config.for_query(
            requested_mode=query.retrieval_mode
        ).effective_mode
        channels = (
            {"vector": []}
            if effective_mode == "vector"
            else {"exact": exact_hits, "bm25": bm25_hits}
        )
        if effective_mode in {"vector", "hybrid"} and self._vector is not None:
            try:
                channels["vector"] = self._vector.retrieve(
                    search_text,
                    candidates,
                    limit=channel_limit,
                )
            except Exception as exc:
                vector_failure = type(exc).__name__
                if not self._retrieval_config.vector_fallback:
                    raise
                channels = {"exact": exact_hits, "bm25": bm25_hits}
        elif effective_mode == "vector":
            if self._retrieval_config.vector_fallback:
                channels = {"exact": exact_hits, "bm25": bm25_hits}
            else:
                channels = {"vector": []}
        hits = self._fusion.fuse(
            channels,
            weights={"exact": 3.0, "bm25": 1.0, "vector": 1.0},
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
                "retrieval_mode": effective_mode,
                "vector_enabled": self._retrieval_config.vector_ready,
                "embedding_model": (
                    self._retrieval_config.embedding_model
                    if self._retrieval_config.vector_ready
                    else None
                ),
                "channel_counts": {name: len(items) for name, items in channels.items()},
                "vector_candidate_count": (
                    len(candidates)
                    if "vector" in channels
                    else None
                ),
                "search_text": search_text,
                "traceback_frames": len(frames),
                "exception": exception,
                "vector_failure": vector_failure,
                "fallback_reason": (
                    "vector_retrieval_failed"
                    if vector_failure
                    else (
                        "vector_provider_unavailable"
                        if effective_mode == "vector" and self._vector is None
                        else None
                    )
                ),
                **(
                    getattr(self._vector, "last_stats", {})
                    if self._vector is not None
                    else {}
                ),
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

    def scan_risks(
        self,
        project: ProjectRef,
        access: AccessContext,
        *,
        now: datetime | None = None,
    ) -> RiskScanResult:
        """Run deterministic rules only after project and ACL filtering."""

        query = ContextQuery(text="authorized project risk scan", project=project, limit=50)
        authorized = self._access.filter(self._index.all(), query, access)
        return self._risks.scan(project, authorized, now=now, complete_snapshot=False)

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

    def resolve_fact(
        self,
        project: ProjectRef,
        access: AccessContext,
        fact_type: FactType,
        *,
        now: datetime | None = None,
    ) -> AuthorityResolution:
        """Select a source after the same project and ACL filtering used by search."""
        query = ContextQuery(text=fact_type.value, project=project, limit=50)
        records = self._authorized_source_records(query, access)
        return resolve_fact(records, fact_type, now=now)

    def source_gaps(
        self,
        project: ProjectRef,
        access: AccessContext,
        *,
        now: datetime | None = None,
    ) -> tuple[SourceGap, ...]:
        """Detect missing, conflicting, stale, and unconfirmed facts within ACL."""
        query = ContextQuery(text="source gaps", project=project, limit=50)
        records = self._authorized_source_records(query, access)
        return detect_gaps(records, project_id=project.project_id, now=now)

    def _authorized_source_records(
        self,
        query: ContextQuery,
        access: AccessContext,
    ) -> tuple[SourceRecord, ...]:
        evidence = self._access.filter(self._index.all(), query, access)
        records = [SourceRecord.from_evidence(item) for item in evidence]
        if self._source_store is not None:
            for item in self._source_store.all(
                tenant_id=query.project.tenant_id,
                project_id=query.project.project_id,
            ):
                if item.access_scope in access.permissions:
                    records.append(item)
        deduplicated = {item.key: item for item in records}
        return tuple(deduplicated.values())

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
