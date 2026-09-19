"""Optional embedding providers and bounded semantic scorers."""

from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime, timezone
from collections.abc import Sequence
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from project_lens.config import Settings, assert_external_calls_allowed
from project_lens.domain.memory import Episode, ProjectMemory
from project_lens.domain.models import ProjectRef
from project_lens.persistence.sqlite import SQLiteDatabase


class EmbeddingProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class EmbeddingCache(Protocol):
    def get_many(
        self, *, kind: str, project: ProjectRef, model_version: str, content_hashes: dict[str, str]
    ) -> dict[str, tuple[float, ...]]: ...

    def put_many(
        self, *, kind: str, project: ProjectRef, model_version: str, vectors: dict[str, tuple[float, ...]], content_hashes: dict[str, str]
    ) -> None: ...


class SQLiteEmbeddingCache:
    """Durable, project-scoped vector cache keyed by content hash and model version."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_embeddings (
                kind TEXT NOT NULL,
                object_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                model_version TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (kind, object_id, tenant_id, project_id, model_version)
            )
            """
        )
        self._database.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_scope ON memory_embeddings (tenant_id, project_id, kind, model_version)"
        )

    def get_many(self, *, kind: str, project: ProjectRef, model_version: str, content_hashes: dict[str, str]) -> dict[str, tuple[float, ...]]:
        if not content_hashes:
            return {}
        placeholders = ",".join("?" for _ in content_hashes)
        rows = self._database.query_all(
            f"SELECT object_id, content_hash, vector_json FROM memory_embeddings WHERE kind = ? AND tenant_id = ? AND project_id = ? AND model_version = ? AND object_id IN ({placeholders})",
            (kind, project.tenant_id, project.project_id, model_version, *content_hashes.keys()),
        )
        result: dict[str, tuple[float, ...]] = {}
        for row in rows:
            object_id = str(row["object_id"])
            if content_hashes.get(object_id) != row["content_hash"]:
                continue
            try:
                vector = json.loads(row["vector_json"])
                if isinstance(vector, list) and vector:
                    result[object_id] = tuple(float(value) for value in vector)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def put_many(self, *, kind: str, project: ProjectRef, model_version: str, vectors: dict[str, tuple[float, ...]], content_hashes: dict[str, str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for object_id, vector in vectors.items():
            self._database.execute(
                "INSERT OR REPLACE INTO memory_embeddings (kind, object_id, tenant_id, project_id, model_version, content_hash, vector_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, object_id, project.tenant_id, project.project_id, model_version, content_hashes.get(object_id, _content_hash(object_id)), json.dumps(vector), now),
            )

    def invalidate(
        self,
        *,
        project: ProjectRef | None = None,
        kind: str | None = None,
        model_version: str | None = None,
    ) -> int:
        """Delete cached vectors for an explicit rebuild or model rotation."""

        clauses: list[str] = []
        parameters: list[object] = []
        if project is not None:
            clauses.extend(("tenant_id = ?", "project_id = ?"))
            parameters.extend((project.tenant_id, project.project_id))
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind)
        if model_version is not None:
            clauses.append("model_version = ?")
            parameters.append(model_version)
        where = " AND ".join(clauses) if clauses else "1 = 1"
        cursor = self._database.execute(f"DELETE FROM memory_embeddings WHERE {where}", tuple(parameters))
        return max(0, int(cursor.rowcount))


class OpenAIEmbeddingProvider:
    """OpenAI-compatible embeddings client, disabled unless external calls are allowed."""

    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", timeout_seconds: float = 10.0, settings: Settings | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._settings = settings

    @property
    def model_version(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int | None:
        return None

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        assert_external_calls_allowed("embedding provider", self._settings)
        payload = json.dumps({"model": self._model, "input": list(texts)}).encode("utf-8")
        request = Request(f"{self._base_url}/embeddings", data=payload, headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError) as exc:
            raise RuntimeError("embedding provider request failed") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise RuntimeError("embedding provider returned an invalid vector count")
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors: list[tuple[float, ...]] = []
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("embedding provider returned an invalid vector")
            vectors.append(tuple(float(value) for value in vector))
        return tuple(vectors)


class MemoryEmbeddingScorer:
    def __init__(self, provider: EmbeddingProvider, *, cache: EmbeddingCache | None = None) -> None:
        self._provider = provider
        self._cache = cache

    def score(self, query: str, candidates: Sequence[ProjectMemory]) -> dict[str, float]:
        return _score_items(self._provider, query, candidates, lambda item: str(item.id), lambda item: item.text, kind="project_memory", project=candidates[0].project if candidates else None, cache=self._cache)


class EpisodeEmbeddingScorer:
    def __init__(self, provider: EmbeddingProvider, *, cache: EmbeddingCache | None = None) -> None:
        self._provider = provider
        self._cache = cache

    def score(self, query: str, candidates: Sequence[Episode]) -> dict[str, float]:
        return _score_items(self._provider, query, candidates, lambda item: str(item.id), lambda item: f"{item.title}\n{item.summary}\n{' '.join(item.tool_names)}", kind="episode", project=candidates[0].project if candidates else None, cache=self._cache)


def _score_items(provider, query, candidates, key_fn, text_fn, *, kind: str, project: ProjectRef | None, cache: EmbeddingCache | None) -> dict[str, float]:
    if not query.strip() or not candidates:
        return {}
    texts = {key_fn(item): text_fn(item)[:4_000] for item in candidates}
    content_hashes = {key: _content_hash(text) for key, text in texts.items()}
    cached: dict[str, tuple[float, ...]] = {}
    if cache is not None and project is not None:
        cached = cache.get_many(kind=kind, project=project, model_version=provider.model_version, content_hashes=content_hashes)
    missing = [key for key in texts if key not in cached]
    query_key = _content_hash(query[:2_000])
    query_cached: dict[str, tuple[float, ...]] = {}
    if cache is not None and project is not None:
        query_cached = cache.get_many(kind=f"{kind}:query", project=project, model_version=provider.model_version, content_hashes={query_key: query_key})
    request_texts = ([query[:2_000]] if not query_cached else []) + [texts[key] for key in missing]
    if request_texts:
        vectors = provider.embed(request_texts)
        if len(vectors) != len(request_texts):
            raise RuntimeError("embedding provider returned an invalid vector count")
        offset = 0
        if not query_cached:
            query_cached = {query_key: vectors[0]}
            offset = 1
            if cache is not None and project is not None:
                cache.put_many(kind=f"{kind}:query", project=project, model_version=provider.model_version, vectors=query_cached, content_hashes={query_key: query_key})
        fresh = {key: vector for key, vector in zip(missing, vectors[offset:], strict=True)}
        cached.update(fresh)
        if cache is not None and project is not None and fresh:
            cache.put_many(kind=kind, project=project, model_version=provider.model_version, vectors=fresh, content_hashes=content_hashes)
    query_vector = next(iter(query_cached.values()))
    return {key: round(_cosine(query_vector, cached[key]), 6) for key in texts if key in cached}


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    if denominator == 0:
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True)) / denominator))


def build_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    if settings.embedding_provider.strip().casefold() != "openai":
        return None
    api_key = settings.embedding_api_key or settings.model_openai_api_key
    model = settings.embedding_model
    if not api_key or not model:
        return None
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        model=model,
        base_url=(
            settings.embedding_base_url
            or settings.model_openai_base_url
            or "https://api.openai.com/v1"
        ),
        timeout_seconds=settings.embedding_timeout_seconds,
        settings=settings,
    )
