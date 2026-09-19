"""Application boundary for authorized project-memory retrieval."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from project_lens.context.memory_authorization import authorize_memory_candidates, authorize_memory_detail
from project_lens.context.memory_retrieval import MemoryRecallResult, retrieve_memories
from project_lens.context.memory_store import MemoryStore
from project_lens.domain.memory import MemoryType, ProjectMemory
from project_lens.domain.models import ProjectRef
from project_lens.project_space.policies import EffectiveAccessScope


class MemoryRetrievalService:
    def __init__(self, store: MemoryStore, *, vector_scorer=None) -> None:
        self.store = store
        self.vector_scorer = vector_scorer

    def search_project_memory(
        self,
        *,
        project: ProjectRef,
        access_scope: EffectiveAccessScope,
        actor_id: str,
        chat_id: str,
        query: str,
        memory_types: tuple[MemoryType, ...] = (),
        subject: str | None = None,
        as_of: datetime | None = None,
        limit: int = 5,
    ) -> MemoryRecallResult:
        if actor_id != access_scope.actor_id or chat_id != access_scope.chat_id:
            raise PermissionError("memory retrieval identity does not match access scope")
        candidates = self.store.search_memories(
            project,
            query,
            memory_types=memory_types,
            subject=subject,
            as_of=as_of,
            limit=128,
        )
        authorized = authorize_memory_candidates(
            candidates, project=project, access_scope=access_scope, as_of=as_of
        )
        return retrieve_memories(query, authorized, limit=limit, vector_scorer=self.vector_scorer)

    def get_memory_detail(
        self,
        *,
        memory_id: UUID,
        project: ProjectRef,
        access_scope: EffectiveAccessScope,
        allow_historical: bool = False,
        as_of: datetime | None = None,
    ) -> ProjectMemory:
        memory = self.store.get_memory(memory_id, project=project, include_inactive=True, as_of=as_of)
        if memory is None:
            raise KeyError(memory_id)
        return authorize_memory_detail(
            memory,
            project=project,
            access_scope=access_scope,
            allow_historical=allow_historical,
            as_of=as_of,
        )
