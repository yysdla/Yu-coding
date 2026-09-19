"""Optional semantic retrieval over already authorized Evidence candidates."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from project_lens.context.embeddings import EmbeddingCache
from project_lens.context.retrieval.types import ChannelHit
from project_lens.domain.models import Evidence


class EvidenceVectorRetriever:
    def __init__(self, provider, *, cache: EmbeddingCache | None = None) -> None:
        self._provider = provider
        self._cache = cache
        self._last_stats: dict[str, float | int | None] = {}

    @property
    def model_version(self) -> str:
        return str(self._provider.model_version)

    @property
    def last_stats(self) -> dict[str, float | int | None]:
        return dict(self._last_stats)

    def retrieve(
        self,
        query: str,
        candidates: Sequence[Evidence],
        *,
        limit: int,
    ) -> list[ChannelHit]:
        if not query.strip() or not candidates:
            return []
        project = candidates[0].project
        texts = {str(item.id): item.content[:4_000] for item in candidates}
        hashes = {key: _hash(value) for key, value in texts.items()}
        cached = (
            self._cache.get_many(
                kind="evidence",
                project=project,
                model_version=self.model_version,
                content_hashes=hashes,
            )
            if self._cache is not None
            else {}
        )
        query_key = _hash(query[:2_000])
        query_cached = (
            self._cache.get_many(
                kind="evidence:query",
                project=project,
                model_version=self.model_version,
                content_hashes={query_key: query_key},
            )
            if self._cache is not None
            else {}
        )
        cache_lookups = len(texts) + 1 if self._cache is not None else 0
        cache_hits = len(cached) + int(bool(query_cached))
        self._last_stats = {
            "embedding_cache_lookups": cache_lookups,
            "embedding_cache_hits": cache_hits,
            "embedding_cache_hit_rate": (
                cache_hits / cache_lookups if cache_lookups else None
            ),
        }
        missing = [key for key in texts if key not in cached]
        request = ([] if query_cached else [query[:2_000]]) + [texts[key] for key in missing]
        if request:
            vectors = self._provider.embed(request)
            if len(vectors) != len(request):
                raise RuntimeError("embedding provider returned an invalid vector count")
            offset = 0
            if not query_cached:
                query_cached = {query_key: _valid_vector(vectors[0])}
                offset = 1
                if self._cache is not None:
                    self._cache.put_many(
                        kind="evidence:query",
                        project=project,
                        model_version=self.model_version,
                        vectors=query_cached,
                        content_hashes={query_key: query_key},
                    )
            fresh = {
                key: _valid_vector(vector)
                for key, vector in zip(missing, vectors[offset:], strict=True)
            }
            cached.update(fresh)
            if self._cache is not None and fresh:
                self._cache.put_many(
                    kind="evidence",
                    project=project,
                    model_version=self.model_version,
                    vectors=fresh,
                    content_hashes=hashes,
                )
        if not query_cached:
            return []
        query_vector = next(iter(query_cached.values()))
        hits = [
            ChannelHit(
                evidence=item,
                score=_cosine(query_vector, cached[str(item.id)]),
            )
            for item in candidates
            if str(item.id) in cached
        ]
        hits.sort(key=lambda hit: (-hit.score, hit.evidence.source.source_id))
        return hits[: max(1, int(limit))]


def _valid_vector(vector) -> tuple[float, ...]:
    values = tuple(float(value) for value in vector)
    if not values:
        raise RuntimeError("embedding provider returned an empty vector")
    return values


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return max(
        0.0,
        min(
            1.0,
            sum(a * b for a, b in zip(left, right, strict=True)) / denominator,
        ),
    )


__all__ = ["EvidenceVectorRetriever"]
