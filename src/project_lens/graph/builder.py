"""Build an explainable project graph from indexed evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable

from project_lens.domain.models import Evidence, EvidenceType
from project_lens.graph.models import GraphEdge, GraphNode, GraphNodeKind, ProjectGraph


class EvidenceGraphBuilder:
    """Builds deterministic relations without requiring a graph database."""

    def build(self, evidence: Iterable[Evidence]) -> ProjectGraph:
        items = tuple(evidence)
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        for item in items:
            evidence_id = _evidence_node_id(item)
            nodes[evidence_id] = GraphNode(
                id=evidence_id,
                kind=GraphNodeKind.EVIDENCE,
                label=item.source.source_id,
                metadata={
                    "evidence_type": item.type.value,
                    "source_system": item.source.system,
                    "project_id": item.project.project_id,
                },
            )
            project_id = f"project:{item.project.tenant_id}:{item.project.project_id}"
            nodes.setdefault(
                project_id,
                GraphNode(
                    id=project_id,
                    kind=GraphNodeKind.PROJECT,
                    label=item.project.project_id,
                ),
            )
            edges.append(
                GraphEdge(
                    source=evidence_id,
                    target=project_id,
                    relation="belongs_to",
                    observed_at=item.observed_at,
                )
            )

            if item.project.service:
                service_id = f"service:{item.project.tenant_id}:{item.project.service}"
                nodes.setdefault(
                    service_id,
                    GraphNode(
                        id=service_id,
                        kind=GraphNodeKind.SERVICE,
                        label=item.project.service,
                    ),
                )
                edges.append(
                    GraphEdge(
                        source=evidence_id,
                        target=service_id,
                        relation="concerns",
                        observed_at=item.observed_at,
                    )
                )

            document_id = _document_node_id(item)
            if document_id is not None:
                document_label = str(
                    item.metadata.get("title")
                    or item.metadata.get("document_title")
                    or item.source.source_id
                )
                _ensure_document_node(
                    nodes,
                    node_id=document_id,
                    label=document_label,
                    evidence_id=str(item.id),
                    source_system=item.source.system,
                )
                edges.append(
                    GraphEdge(
                        source=evidence_id,
                        target=document_id,
                        relation="describes",
                        observed_at=item.observed_at,
                    )
                )
                edges.append(
                    GraphEdge(
                        source=document_id,
                        target=project_id,
                        relation="describes",
                        observed_at=item.observed_at,
                    )
                )
                for module_label in _metadata_values(item.metadata, "describes_modules"):
                    module_id = _module_node_id(item, module_label)
                    _ensure_module_node(
                        nodes,
                        node_id=module_id,
                        label=module_label,
                        evidence_id=str(item.id),
                    )
                    edges.append(
                        GraphEdge(
                            source=document_id,
                            target=module_id,
                            relation="describes",
                            observed_at=item.observed_at,
                        )
                    )
                described_services = _metadata_values(
                    item.metadata,
                    "describes_services",
                )
                if item.project.service and not described_services:
                    described_services = (item.project.service,)
                for service_label in described_services:
                    service_id = f"service:{item.project.tenant_id}:{service_label}"
                    _ensure_service_node(
                        nodes,
                        node_id=service_id,
                        label=service_label,
                        evidence_id=str(item.id),
                    )
                    edges.append(
                        GraphEdge(
                            source=document_id,
                            target=service_id,
                            relation="describes",
                            observed_at=item.observed_at,
                        )
                    )

            symbol = item.metadata.get("symbol")
            if item.type == EvidenceType.CODE and symbol:
                symbol_id = f"symbol:{item.source.source_id}:{symbol}"
                nodes.setdefault(
                    symbol_id,
                    GraphNode(
                        id=symbol_id,
                        kind=GraphNodeKind.SYMBOL,
                        label=str(symbol),
                        metadata={
                            "file": item.metadata.get("file"),
                            "evidence_id": str(item.id),
                        },
                    ),
                )
                edges.append(
                    GraphEdge(
                        source=evidence_id,
                        target=symbol_id,
                        relation="defines",
                        observed_at=item.observed_at,
                    )
                )
                if item.project.service:
                    service_id = f"service:{item.project.tenant_id}:{item.project.service}"
                    module_id = f"module:{item.project.tenant_id}:{item.metadata.get('file') or symbol}"
                    nodes.setdefault(
                        module_id,
                        GraphNode(
                            id=module_id,
                            kind=GraphNodeKind.MODULE,
                            label=str(item.metadata.get("file") or symbol),
                            metadata={"evidence_id": str(item.id)},
                        ),
                    )
                    edges.append(
                        GraphEdge(
                            source=service_id,
                            target=module_id,
                            relation="owns_module",
                            observed_at=item.observed_at,
                        )
                    )
                    edges.append(
                        GraphEdge(
                            source=module_id,
                            target=symbol_id,
                            relation="defines",
                            observed_at=item.observed_at,
                        )
                    )

            endpoint_path = _endpoint_path(item)
            if endpoint_path is not None:
                endpoint_id = _endpoint_node_id(item, endpoint_path)
                method = str(
                    item.metadata.get("http_method")
                    or item.metadata.get("method")
                    or ""
                ).upper()
                _ensure_endpoint_node(
                    nodes,
                    node_id=endpoint_id,
                    label=endpoint_path,
                    evidence_id=str(item.id),
                    method=method,
                    path=endpoint_path,
                )
                edges.append(
                    GraphEdge(
                        source=evidence_id,
                        target=endpoint_id,
                        relation="describes",
                        observed_at=item.observed_at,
                    )
                )
                if document_id is not None:
                    edges.append(
                        GraphEdge(
                            source=document_id,
                            target=endpoint_id,
                            relation="describes",
                            observed_at=item.observed_at,
                        )
                    )
                if item.project.service:
                    service_id = f"service:{item.project.tenant_id}:{item.project.service}"
                    edges.append(
                        GraphEdge(
                            source=service_id,
                            target=endpoint_id,
                            relation="exposes",
                            observed_at=item.observed_at,
                        )
                    )
                    edges.append(
                        GraphEdge(
                            source=endpoint_id,
                            target=service_id,
                            relation="belongs_to",
                            observed_at=item.observed_at,
                        )
                    )
                module_label = str(
                    item.metadata.get("module") or item.metadata.get("file") or ""
                ).strip()
                if module_label:
                    module_id = _module_node_id(item, module_label)
                    _ensure_module_node(
                        nodes,
                        node_id=module_id,
                        label=module_label,
                        evidence_id=str(item.id),
                    )
                    edges.append(
                        GraphEdge(
                            source=endpoint_id,
                            target=module_id,
                            relation="implemented_by",
                            observed_at=item.observed_at,
                        )
                    )
                if symbol:
                    symbol_id = f"symbol:{item.source.source_id}:{symbol}"
                    nodes.setdefault(
                        symbol_id,
                        GraphNode(
                            id=symbol_id,
                            kind=GraphNodeKind.SYMBOL,
                            label=str(symbol),
                            metadata={
                                "file": item.metadata.get("file"),
                                "evidence_id": str(item.id),
                            },
                        ),
                    )
                    edges.append(
                        GraphEdge(
                            source=endpoint_id,
                            target=symbol_id,
                            relation="implemented_by",
                            observed_at=item.observed_at,
                        )
                    )

            if item.type == EvidenceType.COMMIT:
                commit_sha = str(item.metadata.get("commit_sha") or item.source.source_id)
                commit_id = f"commit:{item.project.tenant_id}:{commit_sha}"
                nodes.setdefault(
                    commit_id,
                    GraphNode(
                        id=commit_id,
                        kind=GraphNodeKind.COMMIT,
                        label=commit_sha[:12],
                        metadata={"evidence_id": str(item.id), "branch": item.metadata.get("branch")},
                    ),
                )
                edges.append(
                    GraphEdge(
                        source=evidence_id,
                        target=commit_id,
                        relation="describes",
                        observed_at=item.observed_at,
                    )
                )
                files = item.metadata.get("files_changed") or []
                if isinstance(files, list):
                    for path in files[:10]:
                        module_id = f"module:{item.project.tenant_id}:{path}"
                        nodes.setdefault(
                            module_id,
                            GraphNode(
                                id=module_id,
                                kind=GraphNodeKind.MODULE,
                                label=str(path),
                                metadata={"evidence_id": str(item.id)},
                            ),
                        )
                        edges.append(
                            GraphEdge(
                                source=commit_id,
                                target=module_id,
                                relation="changes",
                                observed_at=item.observed_at,
                            )
                        )
                        if item.project.service:
                            service_id = f"service:{item.project.tenant_id}:{item.project.service}"
                            edges.append(
                                GraphEdge(
                                    source=module_id,
                                    target=service_id,
                                    relation="belongs_to",
                                    observed_at=item.observed_at,
                                )
                            )

            if item.type == EvidenceType.INCIDENT and item.project.service:
                incident_key = str(
                    item.metadata.get("incident_id")
                    or item.metadata.get("id")
                    or item.source.source_id
                )
                incident_id = f"incident:{item.project.tenant_id}:{incident_key}"
                service_id = f"service:{item.project.tenant_id}:{item.project.service}"
                nodes.setdefault(
                    incident_id,
                    GraphNode(
                        id=incident_id,
                        kind=GraphNodeKind.INCIDENT,
                        label=incident_key,
                        metadata={"evidence_id": str(item.id)},
                    ),
                )
                edges.append(
                    GraphEdge(
                        source=incident_id,
                        target=service_id,
                        relation="affects",
                        observed_at=item.observed_at,
                    )
                )
                edges.append(
                    GraphEdge(
                        source=evidence_id,
                        target=incident_id,
                        relation="describes",
                        observed_at=item.observed_at,
                    )
                )
                related_commit = item.metadata.get("related_commit_sha")
                if related_commit:
                    commit_id = f"commit:{item.project.tenant_id}:{related_commit}"
                    nodes.setdefault(
                        commit_id,
                        GraphNode(
                            id=commit_id,
                            kind=GraphNodeKind.COMMIT,
                            label=str(related_commit)[:12],
                            metadata={"evidence_id": str(item.id)},
                        ),
                    )
                    edges.append(
                        GraphEdge(
                            source=incident_id,
                            target=commit_id,
                            relation="caused_by",
                            observed_at=item.observed_at,
                        )
                    )

            if item.type == EvidenceType.TASK:
                kind = str(item.metadata.get("kind") or "task")
                if kind == "release":
                    release_key = str(
                        item.metadata.get("release_id") or item.source.source_id
                    )
                    release_id = f"release:{item.project.tenant_id}:{release_key}"
                    nodes.setdefault(
                        release_id,
                        GraphNode(
                            id=release_id,
                            kind=GraphNodeKind.RELEASE,
                            label=str(item.metadata.get("version") or release_key),
                            metadata={"evidence_id": str(item.id)},
                        ),
                    )
                    edges.append(
                        GraphEdge(
                            source=evidence_id,
                            target=release_id,
                            relation="describes",
                            observed_at=item.observed_at,
                        )
                    )
                    commit_shas = item.metadata.get("commit_shas") or []
                    if isinstance(commit_shas, list):
                        for sha in commit_shas[:10]:
                            commit_id = f"commit:{item.project.tenant_id}:{sha}"
                            nodes.setdefault(
                                commit_id,
                                GraphNode(
                                    id=commit_id,
                                    kind=GraphNodeKind.COMMIT,
                                    label=str(sha)[:12],
                                    metadata={"evidence_id": str(item.id)},
                                ),
                            )
                            edges.append(
                                GraphEdge(
                                    source=release_id,
                                    target=commit_id,
                                    relation="includes",
                                    observed_at=item.observed_at,
                                )
                            )
                else:
                    task_key = str(item.metadata.get("task_id") or item.source.source_id)
                    task_id = f"task:{item.project.tenant_id}:{task_key}"
                    nodes.setdefault(
                        task_id,
                        GraphNode(
                            id=task_id,
                            kind=GraphNodeKind.TASK,
                            label=str(item.metadata.get("title") or task_key),
                            metadata={"evidence_id": str(item.id)},
                        ),
                    )
                    edges.append(
                        GraphEdge(
                            source=evidence_id,
                            target=task_id,
                            relation="describes",
                            observed_at=item.observed_at,
                        )
                    )
                    related_incident = item.metadata.get("related_incident_id")
                    if related_incident:
                        incident_id = (
                            f"incident:{item.project.tenant_id}:{related_incident}"
                        )
                        nodes.setdefault(
                            incident_id,
                            GraphNode(
                                id=incident_id,
                                kind=GraphNodeKind.INCIDENT,
                                label=str(related_incident),
                                metadata={"evidence_id": str(item.id)},
                            ),
                        )
                        edges.append(
                            GraphEdge(
                                source=task_id,
                                target=incident_id,
                                relation="tracks",
                                observed_at=item.observed_at,
                            )
                        )
                    related_commit = item.metadata.get("related_commit_sha")
                    if related_commit:
                        commit_id = (
                            f"commit:{item.project.tenant_id}:{related_commit}"
                        )
                        nodes.setdefault(
                            commit_id,
                            GraphNode(
                                id=commit_id,
                                kind=GraphNodeKind.COMMIT,
                                label=str(related_commit)[:12],
                                metadata={"evidence_id": str(item.id)},
                            ),
                        )
                        edges.append(
                            GraphEdge(
                                source=task_id,
                                target=commit_id,
                                relation="linked_to",
                                observed_at=item.observed_at,
                            )
                        )

            if str(item.metadata.get("kind") or "") == "service_dependency":
                from_service = str(item.metadata.get("from_service") or "").strip()
                to_service = str(item.metadata.get("to_service") or "").strip()
                relation = str(item.metadata.get("relation") or "depends_on").strip()
                if from_service and to_service and relation == "depends_on":
                    from_id = f"service:{item.project.tenant_id}:{from_service}"
                    to_id = f"service:{item.project.tenant_id}:{to_service}"
                    _ensure_service_node(
                        nodes,
                        node_id=from_id,
                        label=from_service,
                        evidence_id=str(item.id),
                    )
                    _ensure_service_node(
                        nodes,
                        node_id=to_id,
                        label=to_service,
                        evidence_id=str(item.id),
                    )
                    edges.append(
                        GraphEdge(
                            source=from_id,
                            target=to_id,
                            relation="depends_on",
                            observed_at=item.observed_at,
                        )
                    )
                    edges.append(
                        GraphEdge(
                            source=evidence_id,
                            target=from_id,
                            relation="describes",
                            observed_at=item.observed_at,
                        )
                    )

            owner = item.metadata.get("owner") or item.metadata.get("owner_user_id")
            if owner and item.project.service:
                owner_id = f"owner:{item.project.tenant_id}:{owner}"
                service_id = f"service:{item.project.tenant_id}:{item.project.service}"
                nodes.setdefault(
                    owner_id,
                    GraphNode(
                        id=owner_id,
                        kind=GraphNodeKind.OWNER,
                        label=str(owner),
                        metadata={"evidence_id": str(item.id)},
                    ),
                )
                edges.append(
                    GraphEdge(
                        source=owner_id,
                        target=service_id,
                        relation="responsible_for",
                        observed_at=item.observed_at,
                    )
                )

        for left_index, left in enumerate(items):
            for right in items[left_index + 1 :]:
                if left.project != right.project:
                    continue
                overlap = _shared_terms(left.content, right.content)
                if len(overlap) < 2:
                    continue
                edges.append(
                    GraphEdge(
                        source=_evidence_node_id(left),
                        target=_evidence_node_id(right),
                        relation="relates_to",
                        observed_at=max(left.observed_at, right.observed_at),
                        weight=min(1.0, len(overlap) / 5),
                    )
                )

        return ProjectGraph(nodes=tuple(nodes.values()), edges=tuple(_dedupe_edges(edges)))


def _ensure_service_node(
    nodes: dict[str, GraphNode],
    *,
    node_id: str,
    label: str,
    evidence_id: str,
) -> None:
    existing = nodes.get(node_id)
    if existing is None:
        nodes[node_id] = GraphNode(
            id=node_id,
            kind=GraphNodeKind.SERVICE,
            label=label,
            metadata={"evidence_id": evidence_id},
        )
        return
    if existing.metadata.get("evidence_id"):
        return
    nodes[node_id] = existing.model_copy(
        update={"metadata": {**existing.metadata, "evidence_id": evidence_id}}
    )


def _ensure_module_node(
    nodes: dict[str, GraphNode],
    *,
    node_id: str,
    label: str,
    evidence_id: str,
) -> None:
    existing = nodes.get(node_id)
    if existing is None:
        nodes[node_id] = GraphNode(
            id=node_id,
            kind=GraphNodeKind.MODULE,
            label=label,
            metadata={"evidence_id": evidence_id},
        )
        return
    if existing.metadata.get("evidence_id"):
        return
    nodes[node_id] = existing.model_copy(
        update={"metadata": {**existing.metadata, "evidence_id": evidence_id}}
    )


def _ensure_document_node(
    nodes: dict[str, GraphNode],
    *,
    node_id: str,
    label: str,
    evidence_id: str,
    source_system: str,
) -> None:
    nodes.setdefault(
        node_id,
        GraphNode(
            id=node_id,
            kind=GraphNodeKind.DOCUMENT,
            label=label,
            metadata={
                "evidence_id": evidence_id,
                "source_system": source_system,
            },
        ),
    )


def _ensure_endpoint_node(
    nodes: dict[str, GraphNode],
    *,
    node_id: str,
    label: str,
    evidence_id: str,
    method: str,
    path: str,
) -> None:
    nodes.setdefault(
        node_id,
        GraphNode(
            id=node_id,
            kind=GraphNodeKind.ENDPOINT,
            label=label,
            metadata={
                "evidence_id": evidence_id,
                "method": method,
                "path": path,
            },
        ),
    )


def _evidence_node_id(item: Evidence) -> str:
    return f"evidence:{item.id}"


def _document_node_id(item: Evidence) -> str | None:
    if item.type != EvidenceType.DOCUMENT:
        return None
    raw = (
        item.metadata.get("doc_token")
        or item.metadata.get("document_id")
        or item.source.source_id
    )
    value = str(raw or "").strip()
    if not value:
        return None
    return f"document:{item.project.tenant_id}:{value}"


def _endpoint_path(item: Evidence) -> str | None:
    for key in ("endpoint_path", "endpoint", "route", "path"):
        value = str(item.metadata.get(key) or "").strip()
        if value.startswith("/"):
            return value
    return None


def _endpoint_node_id(item: Evidence, endpoint_path: str) -> str:
    method = str(item.metadata.get("http_method") or item.metadata.get("method") or "")
    normalized = f"{method.upper()}:{endpoint_path}".strip(":")
    return f"endpoint:{item.project.tenant_id}:{normalized}"


def _module_node_id(item: Evidence, module_label: str) -> str:
    return f"module:{item.project.tenant_id}:{module_label}"


def _metadata_values(metadata: dict, key: str) -> tuple[str, ...]:
    raw = metadata.get(key)
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,) if raw.strip() else ()
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return (str(raw).strip(),) if str(raw).strip() else ()


def _shared_terms(left: str, right: str) -> set[str]:
    stop_words = {"the", "and", "with", "from", "must", "that", "this", "for"}
    left_terms = {
        term
        for term in re.findall(r"[a-z][a-z0-9_.-]{2,}", left.lower())
        if term not in stop_words
    }
    right_terms = {
        term
        for term in re.findall(r"[a-z][a-z0-9_.-]{2,}", right.lower())
        if term not in stop_words
    }
    return left_terms & right_terms


def _dedupe_edges(edges: Iterable[GraphEdge]) -> tuple[GraphEdge, ...]:
    unique: dict[tuple[str, str, str], GraphEdge] = {}
    for edge in edges:
        unique[(edge.source, edge.target, edge.relation)] = edge
    return tuple(unique.values())
