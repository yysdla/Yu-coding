"""Read-only graph queries over ACL-filtered project graphs."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from project_lens.domain.models import Evidence, GraphEvidence, ProjectRef
from project_lens.graph.builder import EvidenceGraphBuilder
from project_lens.graph.models import GraphEdge, GraphNode, GraphNodeKind, ProjectGraph


class GraphQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start_kind: GraphNodeKind | None = None
    start_label: str | None = None
    relation: str | None = None
    end_kind: GraphNodeKind | None = None
    max_depth: int = Field(default=3, ge=1, le=6)
    limit: int = Field(default=10, ge=1, le=50)


class GraphQueryService:
    def __init__(self, builder: EvidenceGraphBuilder | None = None) -> None:
        self._builder = builder or EvidenceGraphBuilder()

    def query(
        self,
        evidence: tuple[Evidence, ...],
        *,
        project: ProjectRef,
        query: GraphQuery,
    ) -> tuple[GraphEvidence, ...]:
        scoped = tuple(
            item
            for item in evidence
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
        )
        if not scoped:
            return ()
        graph = self._builder.build(scoped)
        starts = _start_nodes(graph, query)
        results: list[GraphEvidence] = []
        for start in starts:
            for nodes, relations in sorted(
                _paths_from(graph, start, query),
                key=lambda item: (len(item[0]), item[1]),
            ):
                item = _to_graph_evidence(nodes, relations, project=project, evidence=scoped)
                if not item.evidence_ids:
                    continue
                results.append(item)
                if len(results) >= query.limit:
                    return tuple(results)
        return tuple(results)


def _start_nodes(graph: ProjectGraph, query: GraphQuery) -> tuple[GraphNode, ...]:
    nodes = graph.nodes
    if query.start_kind is not None:
        nodes = tuple(node for node in nodes if node.kind == query.start_kind)
    if query.start_label:
        needle = query.start_label.casefold()
        nodes = tuple(node for node in nodes if needle in node.label.casefold())
    return nodes


def _paths_from(
    graph: ProjectGraph,
    start: GraphNode,
    query: GraphQuery,
) -> list[tuple[tuple[GraphNode, ...], tuple[str, ...]]]:
    by_id = {node.id: node for node in graph.nodes}
    outgoing: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append(edge)

    found: list[tuple[tuple[GraphNode, ...], tuple[str, ...]]] = []

    def dfs(
        node: GraphNode,
        path: tuple[GraphNode, ...],
        relations: tuple[str, ...],
        visited: set[str],
    ) -> None:
        if len(path) > 1 and _matches_end(node, query):
            found.append((path, relations))
        if len(path) > query.max_depth:
            return
        for edge in outgoing.get(node.id, []):
            if query.relation and edge.relation != query.relation and len(path) == 1:
                continue
            if edge.target in visited:
                continue
            nxt = by_id.get(edge.target)
            if nxt is None:
                continue
            dfs(
                nxt,
                path + (nxt,),
                relations + (edge.relation,),
                visited | {edge.target},
            )

    dfs(start, (start,), (), {start.id})
    return found


def _matches_end(node: GraphNode, query: GraphQuery) -> bool:
    if query.end_kind is None:
        return True
    return node.kind == query.end_kind


def _to_graph_evidence(
    path: tuple[GraphNode, ...],
    relations: tuple[str, ...],
    *,
    project: ProjectRef,
    evidence: tuple[Evidence, ...],
) -> GraphEvidence:
    evidence_by_id = {item.id: item for item in evidence}
    evidence_ids: list[UUID] = []
    for node in path:
        if node.kind == GraphNodeKind.EVIDENCE and node.id.startswith("evidence:"):
            raw = node.id.removeprefix("evidence:")
            try:
                evidence_id = UUID(raw)
            except ValueError:
                continue
            if evidence_id in evidence_by_id:
                evidence_ids.append(evidence_id)
        linked = node.metadata.get("evidence_id")
        if linked:
            try:
                evidence_id = UUID(str(linked))
            except ValueError:
                continue
            if evidence_id in evidence_by_id:
                evidence_ids.append(evidence_id)
    labels = tuple(node.label for node in path)
    unique_ids = tuple(dict.fromkeys(evidence_ids))
    cited = tuple(evidence_by_id[item_id] for item_id in unique_ids if item_id in evidence_by_id)
    return GraphEvidence(
        project=project,
        path_labels=labels,
        relations=relations,
        node_ids=tuple(node.id for node in path),
        evidence_ids=unique_ids,
        summary=" -> ".join(labels),
        cited_evidence=cited,
    )
