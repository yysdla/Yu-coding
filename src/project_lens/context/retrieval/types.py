"""Internal retrieval channel result types."""

from dataclasses import dataclass

from project_lens.domain.models import Evidence


@dataclass(frozen=True)
class ChannelHit:
    evidence: Evidence
    score: float

