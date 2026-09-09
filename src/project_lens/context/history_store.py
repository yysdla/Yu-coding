"""Durable storage for derived Episode and MemoryObservation records."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from project_lens.context.history_memory import derive_episode
from project_lens.domain.memory import Episode, MemoryObservation
from project_lens.domain.models import AgentRun, ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase
from project_lens.runtime.events import AgentEvent


class HistoryMemoryStore(Protocol):
    def upsert_run(self, run: AgentRun, events: tuple[AgentEvent, ...]) -> Episode: ...

    def get_episode(self, run_id: UUID) -> Episode | None: ...

    def list_observations(self, run_id: UUID) -> tuple[MemoryObservation, ...]: ...

    def search_episodes(
        self, project: ProjectRef, query: str, *, limit: int = 128
    ) -> tuple[Episode, ...]: ...

    def prune_before(self, project: ProjectRef, cutoff) -> int: ...


class HistoryVectorScorer(Protocol):
    """Optional semantic scorer for already project-scoped episodes."""

    def score(self, query: str, candidates: Sequence[Episode]) -> dict[str, float]: ...


class InMemoryHistoryMemoryStore:
    def __init__(self, *, vector_scorer: HistoryVectorScorer | None = None) -> None:
        self._episodes: dict[UUID, Episode] = {}
        self._observations: dict[UUID, MemoryObservation] = {}
        self._vector_scorer = vector_scorer

    @property
    def uses_vector_scoring(self) -> bool:
        return self._vector_scorer is not None

    def upsert_run(self, run: AgentRun, events: tuple[AgentEvent, ...]) -> Episode:
        episode, observations = derive_episode(run, events)
        self._episodes[run.id] = episode
        for observation in observations:
            self._observations[observation.id] = observation
        return episode

    def get_episode(self, run_id: UUID) -> Episode | None:
        return self._episodes.get(run_id)

    def list_observations(self, run_id: UUID) -> tuple[MemoryObservation, ...]:
        return tuple(item for item in self._observations.values() if item.run_id == run_id)

    def search_episodes(
        self, project: ProjectRef, query: str, *, limit: int = 128
    ) -> tuple[Episode, ...]:
        terms = tuple(item.casefold() for item in query.split() if item.strip())
        episodes = [
            item for item in self._episodes.values()
            if item.project.tenant_id == project.tenant_id
            and item.project.project_id == project.project_id
            and (
                not terms
                or self._vector_scorer is not None
                or all(term in f"{item.title} {item.summary} {' '.join(item.tool_names)}".casefold() for term in terms)
            )
        ]
        return _rank_episodes(episodes, query, limit=limit, vector_scorer=self._vector_scorer)

    def prune_before(self, project: ProjectRef, cutoff) -> int:
        removed_runs = {
            run_id
            for run_id, episode in self._episodes.items()
            if episode.project.tenant_id == project.tenant_id
            and episode.project.project_id == project.project_id
            and episode.ended_at < cutoff
        }
        for run_id in removed_runs:
            self._episodes.pop(run_id, None)
        for observation_id, observation in tuple(self._observations.items()):
            if observation.run_id in removed_runs:
                self._observations.pop(observation_id, None)
        return len(removed_runs)


class SQLiteHistoryMemoryStore:
    def __init__(self, database: SQLiteDatabase, *, vector_scorer: HistoryVectorScorer | None = None) -> None:
        self._database = database
        self._vector_scorer = vector_scorer
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_episodes (
                run_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._fts_enabled = True
        try:
            self._database.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_episodes_fts USING fts5(
                    run_id UNINDEXED, tenant_id UNINDEXED, project_id UNINDEXED, search_text
                )
                """
            )
        except Exception:
            self._fts_enabled = False
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_observations (
                observation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._database.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_episodes_project ON memory_episodes (tenant_id, project_id)"
        )
        self._database.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_observations_run ON memory_observations (run_id)"
        )

    @property
    def uses_vector_scoring(self) -> bool:
        return self._vector_scorer is not None

    def upsert_run(self, run: AgentRun, events: tuple[AgentEvent, ...]) -> Episode:
        episode, observations = derive_episode(run, events)
        self._database.execute(
            "INSERT OR REPLACE INTO memory_episodes (run_id, tenant_id, project_id, payload) VALUES (?, ?, ?, ?)",
            (str(run.id), run.project.tenant_id, run.project.project_id, episode.model_dump_json()),
        )
        if self._fts_enabled:
            self._database.execute("DELETE FROM memory_episodes_fts WHERE run_id = ?", (str(run.id),))
            self._database.execute(
                "INSERT INTO memory_episodes_fts (run_id, tenant_id, project_id, search_text) VALUES (?, ?, ?, ?)",
                (str(run.id), run.project.tenant_id, run.project.project_id, _episode_search_text(episode)),
            )
        self._database.execute("DELETE FROM memory_observations WHERE run_id = ?", (str(run.id),))
        for observation in observations:
            self._database.execute(
                "INSERT OR REPLACE INTO memory_observations (observation_id, run_id, tenant_id, project_id, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    str(observation.id),
                    str(run.id),
                    run.project.tenant_id,
                    run.project.project_id,
                    observation.model_dump_json(),
                ),
            )
        return episode

    def get_episode(self, run_id: UUID) -> Episode | None:
        row = self._database.query_one(
            "SELECT payload FROM memory_episodes WHERE run_id = ?",
            (str(run_id),),
        )
        return Episode.model_validate_json(row["payload"]) if row else None

    def list_observations(self, run_id: UUID) -> tuple[MemoryObservation, ...]:
        rows = self._database.query_all(
            "SELECT payload FROM memory_observations WHERE run_id = ? ORDER BY observation_id",
            (str(run_id),),
        )
        return tuple(MemoryObservation.model_validate_json(row["payload"]) for row in rows)

    def search_episodes(
        self, project: ProjectRef, query: str, *, limit: int = 128
    ) -> tuple[Episode, ...]:
        bounded = max(1, int(limit))
        if not self._fts_enabled or not query.strip():
            rows = self._database.query_all(
                "SELECT payload FROM memory_episodes WHERE tenant_id = ? AND project_id = ?",
                (project.tenant_id, project.project_id),
            )
            episodes = [Episode.model_validate_json(row["payload"]) for row in rows]
            return _rank_episodes(episodes, query, limit=bounded, vector_scorer=self._vector_scorer)
        tokens = [item for item in query.casefold().split() if item.strip()]
        match = " OR ".join(f'"{item.replace(chr(34), "")}"' for item in tokens[:16])
        try:
            rows = self._database.query_all(
                "SELECT run_id FROM memory_episodes_fts WHERE tenant_id = ? AND project_id = ? AND memory_episodes_fts MATCH ? ORDER BY bm25(memory_episodes_fts) LIMIT ?",
                (project.tenant_id, project.project_id, match, bounded),
            )
        except Exception:
            return self.search_episodes(project, "", limit=bounded)
        result: list[Episode] = []
        for row in rows:
            episode = self.get_episode(UUID(str(row["run_id"])))
            if episode is not None:
                result.append(episode)
        if not result:
            # SQLite FTS tokenization is unreliable for unsegmented CJK text.
            # Fall back to the bounded project partition and apply the
            # application-level matcher instead of treating a tokenizer miss
            # as proof that no historical episode exists.
            scoped_rows = self._database.query_all(
                "SELECT payload FROM memory_episodes WHERE tenant_id = ? AND project_id = ? ORDER BY rowid DESC LIMIT ?",
                (project.tenant_id, project.project_id, max(128, bounded * 16)),
            )
            result = [Episode.model_validate_json(row["payload"]) for row in scoped_rows]
        if self._vector_scorer is None:
            return _rank_episodes(result, query, limit=bounded, vector_scorer=None)
        # FTS is only a candidate generator. Semantic scoring must see the
        # bounded project partition, otherwise a synonym absent from FTS would
        # never be eligible.
        rows = self._database.query_all(
            "SELECT payload FROM memory_episodes WHERE tenant_id = ? AND project_id = ? ORDER BY rowid DESC LIMIT ?",
            (project.tenant_id, project.project_id, max(128, bounded * 16)),
        )
        scoped = [Episode.model_validate_json(row["payload"]) for row in rows]
        return _rank_episodes(scoped, query, limit=bounded, vector_scorer=self._vector_scorer)

    def prune_before(self, project: ProjectRef, cutoff) -> int:
        rows = self._database.query_all(
            "SELECT run_id FROM memory_episodes WHERE tenant_id = ? AND project_id = ?",
            (project.tenant_id, project.project_id),
        )
        removed: list[str] = []
        for row in rows:
            episode = self.get_episode(UUID(str(row["run_id"])))
            if episode is not None and episode.ended_at < cutoff:
                removed.append(str(episode.run_id))
        for run_id in removed:
            self._database.execute("DELETE FROM memory_episodes WHERE run_id = ?", (run_id,))
            if self._fts_enabled:
                self._database.execute("DELETE FROM memory_episodes_fts WHERE run_id = ?", (run_id,))
            self._database.execute("DELETE FROM memory_observations WHERE run_id = ?", (run_id,))
        return len(removed)


def _episode_search_text(episode: Episode) -> str:
    return " ".join((episode.title, episode.summary, *episode.tool_names, episode.status))


def _rank_episodes(
    episodes: Sequence[Episode],
    query: str,
    *,
    limit: int,
    vector_scorer: HistoryVectorScorer | None,
) -> tuple[Episode, ...]:
    bounded = max(1, int(limit))
    if not episodes:
        return ()
    semantic_scores: dict[str, float] = {}
    if vector_scorer is not None:
        try:
            semantic_scores = {str(key): max(0.0, float(value)) for key, value in vector_scorer.score(query, episodes).items()}
        except Exception:
            # Retrieval must remain available when an embedding backend is down.
            semantic_scores = {}
    terms = tuple(item.casefold() for item in query.split() if item.strip())
    scored: list[tuple[float, Episode]] = []
    for episode in episodes:
        haystack = f"{episode.title} {episode.summary} {' '.join(episode.tool_names)}".casefold()
        lexical = sum(1 for term in terms if term in haystack) / max(1, len(terms)) if terms else 0.0
        semantic = min(1.0, semantic_scores.get(str(episode.id), 0.0))
        if terms and lexical <= 0 and semantic <= 0:
            continue
        score = 0.65 * lexical + 0.35 * semantic if vector_scorer is not None else lexical
        scored.append((score, episode))
    scored.sort(key=lambda item: (-item[0], -item[1].ended_at.timestamp(), str(item[1].id)))
    return tuple(item[1] for item in scored[:bounded])
