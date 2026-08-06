"""Pre-retrieval access and metadata filtering."""

from __future__ import annotations

from collections.abc import Iterable

from project_lens.context.models import AccessContext, ContextQuery
from project_lens.domain.models import Evidence


class EvidenceAccessPolicy:
    def filter(
        self,
        evidence: Iterable[Evidence],
        query: ContextQuery,
        access: AccessContext,
    ) -> tuple[Evidence, ...]:
        allowed: list[Evidence] = []
        requested_types = set(query.source_types)
        for item in evidence:
            if item.project.tenant_id != access.tenant_id:
                continue
            if item.project.project_id != query.project.project_id:
                continue
            if query.project.service and item.project.service not in {None, query.project.service}:
                continue
            if query.project.environment and item.project.environment not in {
                None,
                query.project.environment,
            }:
                continue
            if requested_types and item.type not in requested_types:
                continue
            if query.time_range and not (
                query.time_range.start <= item.observed_at <= query.time_range.end
            ):
                continue
            if item.access_scope not in access.permissions:
                continue
            allowed.append(item)
        return tuple(allowed)

