"""Weighted reciprocal-rank fusion with stable evidence deduplication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from project_lens.context.models import RetrievalHit
from project_lens.context.retrieval.types import ChannelHit


class ReciprocalRankFusion:
    def __init__(self, *, rank_constant: int = 60) -> None:
        self._rank_constant = rank_constant

    def fuse(
        self,
        channels: Mapping[str, Sequence[ChannelHit]],
        *,
        weights: Mapping[str, float],
        limit: int,
    ) -> list[RetrievalHit]:
        merged: dict[str, dict] = {}
        for channel, hits in channels.items():
            weight = float(weights.get(channel, 0.0))
            for rank, hit in enumerate(hits, start=1):
                key = _stable_key(hit)
                item = merged.setdefault(
                    key,
                    {
                        "evidence": hit.evidence,
                        "score": 0.0,
                        "channels": [],
                        "ranks": {},
                        "scores": {},
                    },
                )
                item["score"] += weight / (self._rank_constant + rank)
                item["channels"].append(channel)
                item["ranks"][channel] = rank
                item["scores"][channel] = round(hit.score, 8)
        ordered = sorted(
            merged.values(),
            key=lambda item: (-item["score"], item["evidence"].source.source_id),
        )
        return [
            RetrievalHit(
                evidence=item["evidence"],
                score=round(item["score"], 8),
                channels=tuple(sorted(set(item["channels"]))),
                channel_ranks=item["ranks"],
                channel_scores=item["scores"],
            )
            for item in ordered[:limit]
        ]


def _stable_key(hit: ChannelHit) -> str:
    evidence = hit.evidence
    return f"{evidence.source.system}:{evidence.source.source_id}:{evidence.content_hash}"
