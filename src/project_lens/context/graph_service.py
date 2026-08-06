"""Context-facing graph query facade kept behind ContextEngine."""

from __future__ import annotations

from project_lens.domain.models import Evidence, GraphEvidence, ProjectRef
from project_lens.graph.query import GraphQuery, GraphQueryService


class ContextGraphService:
    def __init__(self, query_service: GraphQueryService | None = None) -> None:
        self._query_service = query_service or GraphQueryService()

    def query(
        self,
        evidence: tuple[Evidence, ...],
        *,
        project: ProjectRef,
        query: GraphQuery,
    ) -> tuple[GraphEvidence, ...]:
        return self._query_service.query(evidence, project=project, query=query)
