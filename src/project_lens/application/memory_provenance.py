"""Server-side validation and normalization of memory source references."""

from __future__ import annotations

from collections.abc import Iterable

from project_lens.context.source_store import SourceRecordStore
from project_lens.domain.memory import MemorySourceRef
from project_lens.project_space.policies import EffectiveAccessScope


class MemoryProvenanceError(ValueError):
    code = "MEMORY_INVALID_PROVENANCE"


def resolve_source_refs(
    refs: Iterable[MemorySourceRef],
    *,
    tenant_id: str,
    project_id: str,
    access_scope: EffectiveAccessScope,
    source_store: SourceRecordStore,
) -> tuple[MemorySourceRef, ...]:
    """Resolve exact source revisions and replace client metadata with authority."""

    normalized: list[MemorySourceRef] = []
    for ref in refs:
        if ref.tenant_id != tenant_id or ref.project_id != project_id:
            raise MemoryProvenanceError("source reference is outside the trusted project")
        record = source_store.get(tenant_id, project_id, ref.source_id, ref.revision)
        if record is None or record.revoked:
            raise MemoryProvenanceError("source reference is missing or revoked")
        if not _source_readable(record.source_id, record.access_scope, access_scope):
            raise MemoryProvenanceError("source reference is not readable in the current scope")
        normalized.append(
            MemorySourceRef(
                tenant_id=record.tenant_id,
                project_id=record.project_id,
                source_id=record.source_id,
                revision=record.revision,
                content_hash=record.content_hash,
                source_type=record.source_type.value,
            )
        )
    return tuple(normalized)


def _source_readable(source_id: str, access_scope: str, scope: EffectiveAccessScope) -> bool:
    if source_id in scope.forbidden_sources or access_scope in scope.forbidden_sources:
        return False
    if not scope.readable_sources:
        return False
    return any(
        source_id == allowed
        or source_id.startswith(allowed.rstrip("/"))
        or access_scope == allowed
        for allowed in scope.readable_sources
    )
