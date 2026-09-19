"""Centralized authorization for ProjectMemory candidates and details."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from project_lens.domain.memory import MemoryStatus, ProjectMemory
from project_lens.domain.models import ProjectRef
from project_lens.project_space.policies import EffectiveAccessScope


def authorize_memory_candidates(
    memories: Sequence[ProjectMemory],
    *,
    project: ProjectRef,
    access_scope: EffectiveAccessScope,
    as_of: datetime | None = None,
) -> tuple[ProjectMemory, ...]:
    """Filter without exposing denied IDs, titles, or bodies."""

    moment = as_of or datetime.now(timezone.utc)
    return tuple(
        memory
        for memory in memories
        if _allowed(memory, project=project, scope=access_scope, as_of=moment)
    )


def authorize_memory_detail(
    memory: ProjectMemory,
    *,
    project: ProjectRef,
    access_scope: EffectiveAccessScope,
    allow_historical: bool = False,
    as_of: datetime | None = None,
) -> ProjectMemory:
    moment = as_of or datetime.now(timezone.utc)
    if not _allowed(memory, project=project, scope=access_scope, as_of=moment, allow_historical=allow_historical):
        raise PermissionError("memory is not available in the current access scope")
    return memory


def _allowed(memory: ProjectMemory, *, project: ProjectRef, scope: EffectiveAccessScope, as_of: datetime, allow_historical: bool = False) -> bool:
    if memory.project.tenant_id != project.tenant_id or memory.project.project_id != project.project_id:
        return False
    if scope.project.tenant_id != project.tenant_id or scope.project.project_id != project.project_id:
        return False
    if not allow_historical and memory.status is not MemoryStatus.ACTIVE:
        return False
    valid_from = memory.valid_from if memory.valid_from.tzinfo else memory.valid_from.replace(tzinfo=timezone.utc)
    valid_to = memory.valid_to
    if valid_to is not None and valid_to.tzinfo is None:
        valid_to = valid_to.replace(tzinfo=timezone.utc)
    if not allow_historical and (valid_from > as_of or (valid_to is not None and valid_to <= as_of)):
        return False
    if not _visibility_allowed(memory.visibility_scope, scope):
        return False
    readable = set(scope.readable_sources)
    forbidden = set(scope.forbidden_sources)
    for source in memory.source_refs:
        if source.tenant_id != project.tenant_id or source.project_id != project.project_id:
            return False
        if source.source_id in forbidden or (readable and source.source_id not in readable):
            return False
    return True


def _visibility_allowed(values: Sequence[str], scope: EffectiveAccessScope) -> bool:
    if not values:
        return True
    allowed = {
        f"role:{scope.role.value}",
        f"chat:{scope.chat_id}",
        f"actor:{scope.actor_id}",
    }
    return bool(set(values) & allowed)
