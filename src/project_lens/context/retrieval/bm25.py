"""Dependency-free BM25 over authorized evidence candidates."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

from project_lens.context.retrieval.tokenize import tokenize
from project_lens.context.retrieval.types import ChannelHit
from project_lens.domain.models import Evidence


class BM25Retriever:
    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b

    def retrieve(
        self,
        query: str,
        candidates: Sequence[Evidence],
        *,
        limit: int,
    ) -> list[ChannelHit]:
        if not candidates:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        documents = [_evidence_tokens(item) for item in candidates]
        average_length = sum(len(tokens) for tokens in documents) / max(1, len(documents))
        document_frequency: Counter[str] = Counter()
        for tokens in documents:
            document_frequency.update(set(tokens))

        hits: list[ChannelHit] = []
        for evidence, tokens in zip(candidates, documents, strict=True):
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if frequency == 0:
                    continue
                idf = math.log(
                    1 + (len(documents) - document_frequency[token] + 0.5)
                    / (document_frequency[token] + 0.5)
                )
                length_normalization = 1 - self._b + self._b * (
                    len(tokens) / max(1.0, average_length)
                )
                score += idf * (
                    frequency * (self._k1 + 1)
                    / (frequency + self._k1 * length_normalization)
                )
            if score > 0:
                hits.append(ChannelHit(evidence=evidence, score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.evidence.source.source_id))
        return hits[:limit]


def _evidence_tokens(evidence: Evidence) -> list[str]:
    metadata_text = " ".join(
        str(evidence.metadata.get(key) or "")
        for key in ("file", "symbol", "symbol_type", "service", "title")
    )
    return tokenize(f"{evidence.content}\n{evidence.source.source_id}\n{metadata_text}")

