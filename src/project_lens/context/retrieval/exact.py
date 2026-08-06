"""Traceback and source-location-aware code retrieval."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from project_lens.context.retrieval.types import ChannelHit
from project_lens.domain.models import Evidence, EvidenceType


TRACEBACK_FRAME_RE = re.compile(
    r'File\s+["\'](?P<file>[^"\']+)["\'],\s+line\s+(?P<line>\d+)(?:,\s+in\s+(?P<symbol>[^\s]+))?'
)
EXCEPTION_RE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception)):\s*(?P<message>.+)$", re.M)


@dataclass(frozen=True)
class TracebackHint:
    file: str
    line: int
    symbol: str | None = None


def parse_traceback(text: str) -> tuple[tuple[TracebackHint, ...], str | None]:
    frames = tuple(
        TracebackHint(
            file=match.group("file").replace("\\", "/"),
            line=int(match.group("line")),
            symbol=match.group("symbol"),
        )
        for match in TRACEBACK_FRAME_RE.finditer(text)
    )
    exception = EXCEPTION_RE.search(text)
    exception_text = exception.group(0) if exception else None
    return frames, exception_text


def matching_traceback_frame(
    evidence: Evidence, frames: tuple[TracebackHint, ...]
) -> TracebackHint | None:
    """Return the traceback frame whose source line is covered by this evidence."""
    if evidence.type != EvidenceType.CODE:
        return None
    metadata = evidence.metadata
    evidence_file = str(metadata.get("file") or "").replace("\\", "/")
    start_line = int(metadata.get("start_line") or 0)
    end_line = int(metadata.get("end_line") or start_line)
    return next(
        (
            frame
            for frame in frames
            if _same_file(evidence_file, frame.file)
            and start_line <= frame.line <= end_line
        ),
        None,
    )


class ExactCodeRetriever:
    def retrieve(
        self,
        query: str,
        candidates: Sequence[Evidence],
        *,
        limit: int,
    ) -> list[ChannelHit]:
        frames, exception = parse_traceback(query)
        if not frames and not exception:
            return []
        hits: list[ChannelHit] = []
        for evidence in candidates:
            if evidence.type != EvidenceType.CODE:
                continue
            score = _score_code_evidence(evidence, frames, exception)
            if score > 0:
                hits.append(ChannelHit(evidence=evidence, score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.evidence.source.source_id))
        return hits[:limit]


def _score_code_evidence(
    evidence: Evidence,
    frames: tuple[TracebackHint, ...],
    exception: str | None,
) -> float:
    metadata = evidence.metadata
    evidence_file = str(metadata.get("file") or "").replace("\\", "/")
    start_line = int(metadata.get("start_line") or 0)
    end_line = int(metadata.get("end_line") or start_line)
    symbol = str(metadata.get("symbol") or "")
    score = 0.0
    for index, frame in enumerate(frames):
        if not _same_file(evidence_file, frame.file):
            continue
        frame_weight = 8.0 + index
        score = max(score, frame_weight)
        if start_line <= frame.line <= end_line:
            score += 12.0
        if frame.symbol and symbol == frame.symbol:
            score += 5.0
    if exception:
        exception_type = exception.split(":", 1)[0]
        if exception_type in evidence.content:
            score += 1.0
    return score


def _same_file(indexed_file: str, traceback_file: str) -> bool:
    normalized_trace = traceback_file.lstrip("./")
    return indexed_file == normalized_trace or normalized_trace.endswith(f"/{indexed_file}")
