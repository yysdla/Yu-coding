"""Unified, source-aware read facade over Evidence, Memory, History, and Wiki."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from project_lens.context.models import AccessContext, ContextQuery
from project_lens.domain.models import ProjectRef
from project_lens.project_space.policies import EffectiveAccessScope


class SourceLayer(StrEnum):
    EVIDENCE = "evidence"
    SOURCE_RECORD = "source_record"
    PROJECT_MEMORY = "project_memory"
    OBSIDIAN_WIKI = "obsidian_wiki"
    EPISODE = "episode"
    OBSERVATION = "observation"


_LAYER_PRIORITY = {
    SourceLayer.EVIDENCE: 500,
    SourceLayer.SOURCE_RECORD: 490,
    SourceLayer.PROJECT_MEMORY: 400,
    SourceLayer.OBSIDIAN_WIKI: 300,
    SourceLayer.EPISODE: 200,
    SourceLayer.OBSERVATION: 100,
}


@dataclass(frozen=True)
class KnowledgeResult:
    result_id: str
    source_layer: SourceLayer
    title: str
    snippet: str
    score: float
    retrieval_channels: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    provenance: dict[str, Any] | None = None
    status: str = "active"
    derived: bool = False
    historical: bool = False
    detail_available: bool = True
    payload: Any = None


class UnifiedKnowledgeRetrievalService:
    """Keep source-specific authorization, but expose one result vocabulary."""

    def __init__(
        self,
        *,
        context_engine,
        memory_service=None,
        history_store=None,
        wiki_repository=None,
    ) -> None:
        self._context = context_engine
        self._memory = memory_service
        self._history = history_store
        self._wiki = wiki_repository

    def search_evidence(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        query: str,
        limit: int = 8,
    ) -> tuple[KnowledgeResult, ...]:
        bundle = self._context.search(
            ContextQuery(text=query, project=project, limit=min(max(1, limit), 50)),
            access,
        )
        return tuple(
            KnowledgeResult(
                result_id=str(hit.evidence.id),
                source_layer=(
                    SourceLayer.SOURCE_RECORD
                    if hit.evidence.metadata.get("source_record_key")
                    else SourceLayer.EVIDENCE
                ),
                title=str(hit.evidence.metadata.get("title") or hit.evidence.source.source_id),
                snippet=hit.evidence.content[:500],
                score=hit.score,
                retrieval_channels=hit.channels,
                evidence_ids=(str(hit.evidence.id),),
                provenance={
                    "system": hit.evidence.source.system,
                    "source_id": hit.evidence.source.source_id,
                    "content_hash": hit.evidence.content_hash,
                    "revision": hit.evidence.metadata.get("revision"),
                    "source_record_key": hit.evidence.metadata.get("source_record_key"),
                },
                status=str(hit.evidence.metadata.get("status") or "active"),
                payload=hit.evidence,
            )
            for hit in bundle.hits
        )

    def search_memory(
        self,
        *,
        project: ProjectRef,
        access_scope: EffectiveAccessScope,
        actor_id: str,
        chat_id: str,
        query: str,
        limit: int = 8,
    ) -> tuple[KnowledgeResult, ...]:
        if self._memory is None:
            return ()
        recall = self._memory.search_project_memory(
            project=project,
            access_scope=access_scope,
            actor_id=actor_id,
            chat_id=chat_id,
            query=query,
            limit=limit,
        )
        return tuple(
            KnowledgeResult(
                result_id=card.memory_id,
                source_layer=SourceLayer.PROJECT_MEMORY,
                title=card.title,
                snippet=card.snippet,
                score=card.relevance_score,
                retrieval_channels=card.retrieval_channels,
                evidence_ids=card.evidence_ids,
                provenance={"memory_id": card.memory_id},
                status="active",
                payload=card,
            )
            for card in recall.cards
        )

    def search_wiki(
        self,
        *,
        project: ProjectRef,
        query: str,
        limit: int = 8,
    ) -> tuple[KnowledgeResult, ...]:
        if self._wiki is None:
            return ()
        pages = self._wiki.search(project=project, query=query, limit=limit)
        return tuple(
            KnowledgeResult(
                result_id=page.path,
                source_layer=SourceLayer.OBSIDIAN_WIKI,
                title=page.title,
                snippet=page.snippet,
                score=0.0,
                provenance={
                    "path": page.path,
                    "source_keys": list(page.source_keys),
                    "generated_at": page.generated_at,
                },
                status=page.status,
                derived=True,
                detail_available=True,
                payload=page,
            )
            for page in pages
        )

    def search_history(
        self,
        *,
        project: ProjectRef,
        query: str,
        limit: int = 8,
    ) -> tuple[KnowledgeResult, ...]:
        if self._history is None:
            return ()
        episodes = self._history.search_episodes(project, query, limit=limit)
        return tuple(
            KnowledgeResult(
                result_id=str(episode.id),
                source_layer=SourceLayer.EPISODE,
                title=episode.title,
                snippet=episode.summary[:500],
                score=0.0,
                evidence_ids=tuple(str(item) for item in episode.evidence_ids),
                provenance={
                    "run_id": str(episode.run_id),
                    "observed_at": episode.ended_at.isoformat(),
                },
                status=episode.status,
                historical=True,
                detail_available=False,
                payload=episode,
            )
            for episode in episodes
        )

    def search(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        query: str,
        access_scope: EffectiveAccessScope | None = None,
        actor_id: str | None = None,
        chat_id: str | None = None,
        limit: int = 8,
        include_derived: bool = True,
        include_history: bool = False,
    ) -> tuple[KnowledgeResult, ...]:
        results = list(self.search_evidence(project=project, access=access, query=query, limit=limit))
        if access_scope is not None and actor_id is not None and chat_id is not None:
            results.extend(
                self.search_memory(
                    project=project,
                    access_scope=access_scope,
                    actor_id=actor_id,
                    chat_id=chat_id,
                    query=query,
                    limit=limit,
                )
            )
        if include_derived:
            results.extend(self.search_wiki(project=project, query=query, limit=limit))
        if include_history:
            results.extend(self.search_history(project=project, query=query, limit=limit))
        results.sort(
            key=lambda item: (
                -_LAYER_PRIORITY[item.source_layer],
                -item.score,
                item.result_id,
            )
        )
        return tuple(results[: max(1, int(limit))])


__all__ = ["KnowledgeResult", "SourceLayer", "UnifiedKnowledgeRetrievalService"]
