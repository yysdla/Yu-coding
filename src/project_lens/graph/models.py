"""Small, serializable project graph contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GraphNodeKind(StrEnum):
    EVIDENCE = "evidence"
    PROJECT = "project"
    SERVICE = "service"
    SYMBOL = "symbol"
    COMMIT = "commit"
    INCIDENT = "incident"
    OWNER = "owner"
    MODULE = "module"
    TASK = "task"
    RELEASE = "release"
    DOCUMENT = "document"
    ENDPOINT = "endpoint"


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    kind: GraphNodeKind
    label: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    observed_at: datetime | None = None
    weight: float = Field(default=1.0, ge=0)


class ProjectGraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()

    def neighbors(self, node_id: str, *, relation: str | None = None) -> tuple[GraphNode, ...]:
        node_ids = {
            edge.target
            for edge in self.edges
            if edge.source == node_id and (relation is None or edge.relation == relation)
        }
        by_id = {node.id: node for node in self.nodes}
        return tuple(by_id[item] for item in node_ids if item in by_id)

    def related_evidence(self, evidence_id: str) -> tuple[GraphNode, ...]:
        return tuple(
            node
            for node in self.neighbors(evidence_id)
            if node.kind == GraphNodeKind.EVIDENCE
        )
