"""Bounded, project-scoped long-term memory navigation and retrieval."""

from __future__ import annotations

import re
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Protocol

from project_lens.domain.memory import ProjectMemory

DEFAULT_CARD_LIMIT = 8
DEFAULT_CARD_BUDGET = 4_000
DEFAULT_SNIPPET_CHARS = 420
RRF_K = 60
RECENCY_HALF_LIFE_DAYS = 30.0
MMR_LAMBDA = 0.78


class MemoryVectorScorer(Protocol):
    """Optional semantic scorer; candidates must already be authorized."""

    def score(self, query: str, candidates: Sequence[ProjectMemory]) -> dict[str, float]: ...


@dataclass(frozen=True)
class MemorySummaryEntry:
    topic: str
    keywords: tuple[str, ...]
    memory_ids: tuple[str, ...]
    memory_types: tuple[str, ...]
    active_count: int
    last_updated: str | None = None


@dataclass(frozen=True)
class MemoryCard:
    memory_id: str
    memory_type: str
    title: str
    snippet: str
    evidence_ids: tuple[str, ...]
    valid_from: str
    relevance_score: float
    retrieval_channels: tuple[str, ...] = ()
    detail_available: bool = True


@dataclass(frozen=True)
class MemoryRecallResult:
    query: str
    cards: tuple[MemoryCard, ...]
    candidate_count: int
    returned_count: int
    omitted_count: int
    retrieval_mode: str
    budget_chars: int
    conflicts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def build_memory_summary(
    memories: Sequence[ProjectMemory],
    *,
    max_topics: int = 24,
    max_keywords_per_topic: int = 8,
) -> tuple[MemorySummaryEntry, ...]:
    """Build a compact directory from already project/ACL-filtered memories."""

    grouped: dict[str, list[ProjectMemory]] = defaultdict(list)
    for memory in memories:
        grouped[memory.memory_type.value].append(memory)

    entries: list[MemorySummaryEntry] = []
    for topic, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(entries) >= max(1, max_topics):
            break
        frequencies: Counter[str] = Counter()
        for item in items:
            frequencies.update(token for token in _tokens(item.text) if len(token) > 1)
        keywords = [token for token, _ in frequencies.most_common(max(1, max_keywords_per_topic * 2))]
        if topic not in keywords:
            keywords.insert(0, topic)
        latest = max((item.valid_from for item in items), default=None)
        entries.append(
            MemorySummaryEntry(
                topic=topic,
                keywords=tuple(keywords[:max(1, max_keywords_per_topic)]),
                memory_ids=tuple(str(item.id) for item in items[:40]),
                memory_types=(topic,),
                active_count=len(items),
                last_updated=latest.isoformat() if latest is not None else None,
            )
        )
    return tuple(entries)


def render_memory_summary(
    entries: Sequence[MemorySummaryEntry],
    *,
    max_chars: int = 2_500,
) -> str:
    """Render navigational metadata only; never memory or Evidence bodies."""

    if not entries:
        return "project_memory_directory=(none)"
    lines = ["project_memory_directory:"]
    for entry in entries:
        line = (
            f"topic={entry.topic} keywords={','.join(entry.keywords)} "
            f"memory_types={','.join(entry.memory_types)} active_count={entry.active_count}"
        )
        if entry.last_updated:
            line += f" last_updated={entry.last_updated}"
        if sum(len(item) + 1 for item in lines) + len(line) > max(200, max_chars):
            break
        lines.append(line)
    return "\n".join(lines)


def retrieve_memories(
    query: str,
    candidates: Sequence[ProjectMemory],
    *,
    limit: int = DEFAULT_CARD_LIMIT,
    budget_chars: int = DEFAULT_CARD_BUDGET,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
    vector_scorer: MemoryVectorScorer | None = None,
) -> MemoryRecallResult:
    """Rank authorized candidates and return bounded cards."""

    bounded_limit = min(max(1, int(limit)), DEFAULT_CARD_LIMIT)
    bounded_budget = min(max(500, int(budget_chars)), DEFAULT_CARD_BUDGET)
    bounded_snippet = min(max(80, int(snippet_chars)), DEFAULT_SNIPPET_CHARS)
    query_tokens = _tokens(query)
    vector_enabled = vector_scorer is not None
    try:
        vector_scores = vector_scorer.score(query, candidates) if vector_scorer else {}
    except Exception:
        vector_scores = {}
        vector_enabled = False

    lexical_scores = {str(memory.id): _lexical_score(query_tokens, memory) for memory in candidates}
    lexical_rank = {
        memory_id: rank
        for rank, (memory_id, score) in enumerate(
            sorted(lexical_scores.items(), key=lambda item: (-item[1], item[0])), start=1
        )
        if score > 0
    }
    semantic_rank = {
        memory_id: rank
        for rank, (memory_id, score) in enumerate(
            sorted(vector_scores.items(), key=lambda item: (-item[1], item[0])), start=1
        )
        if score > 0
    }
    scored: list[tuple[float, ProjectMemory, tuple[str, ...]]] = []
    for memory in candidates:
        memory_id = str(memory.id)
        lexical = lexical_scores[memory_id]
        semantic = max(0.0, min(1.0, float(vector_scores.get(memory_id, 0.0))))
        if lexical <= 0 and semantic <= 0:
            continue
        rrf = (1.0 / (RRF_K + lexical_rank[memory_id]) if memory_id in lexical_rank else 0.0)
        rrf += (1.0 / (RRF_K + semantic_rank[memory_id]) if memory_id in semantic_rank else 0.0)
        rrf_score = min(1.0, rrf * RRF_K / 2.0)
        recency = _recency_score(memory.valid_from)
        score = min(1.0, 0.78 * rrf_score + 0.12 * recency + 0.10 * max(lexical, semantic))
        channels = tuple(
            name for name, enabled in (("bm25", lexical > 0), ("vector", semantic > 0)) if enabled
        )
        scored.append((score, memory, channels))

    scored.sort(key=lambda item: (-item[0], str(item[1].id)))
    selected: list[MemoryCard] = []
    selected_memories: list[ProjectMemory] = []
    seen: set[str] = set()
    used_chars = 0
    remaining = list(scored)
    while remaining and len(selected) < bounded_limit:
        score, memory, channels = _pick_mmr_candidate(remaining, selected_memories)
        remaining.remove((score, memory, channels))
        normalized = _normalize(memory.text)
        if normalized in seen:
            continue
        seen.add(normalized)
        card = _card(memory, score=score, channels=channels, snippet_chars=bounded_snippet)
        card_cost = len(card.snippet) + 180
        if selected and used_chars + card_cost > bounded_budget:
            break
        selected.append(card)
        selected_memories.append(memory)
        used_chars += card_cost

    conflicts = _detect_conflicts(selected_memories)

    return MemoryRecallResult(
        query=query,
        cards=tuple(selected),
        candidate_count=len(candidates),
        returned_count=len(selected),
        omitted_count=max(0, len(scored) - len(selected)),
        retrieval_mode="hybrid" if vector_enabled else "bm25",
        budget_chars=bounded_budget,
        conflicts=conflicts,
        warnings=("no authorized project memory matched the query",) if not scored else (),
    )


def _card(memory: ProjectMemory, *, score: float, channels: tuple[str, ...], snippet_chars: int) -> MemoryCard:
    text = memory.text.strip().replace("\r\n", " ").replace("\n", " ")
    if len(text) > snippet_chars:
        text = text[: snippet_chars - 1].rstrip() + "…"
    return MemoryCard(
        memory_id=str(memory.id),
        memory_type=memory.memory_type.value,
        title=text[:100],
        snippet=text,
        evidence_ids=tuple(str(item) for item in memory.evidence_ids),
        valid_from=memory.valid_from.isoformat(),
        relevance_score=round(max(0.0, min(1.0, score)), 6),
        retrieval_channels=channels,
    )


def _lexical_score(query_tokens: list[str], memory: ProjectMemory) -> float:
    if not query_tokens:
        return 0.0
    haystack = _tokens(f"{memory.text} {memory.memory_type.value} {' '.join(str(x) for x in memory.evidence_ids)}")
    frequencies = Counter(haystack)
    matched = sum(1 for token in query_tokens if frequencies[token])
    if matched == 0:
        return 0.0
    raw = sum(min(2, frequencies[token]) for token in query_tokens) / max(1, len(query_tokens))
    return min(1.0, raw)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_./:-]+|[\u4e00-\u9fff]", text.casefold())


def _normalize(text: str) -> str:
    return "".join(text.casefold().split())


def _recency_score(valid_from: datetime) -> float:
    now = datetime.now(timezone.utc)
    timestamp = valid_from if valid_from.tzinfo is not None else valid_from.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - timestamp).total_seconds() / 86_400.0)
    return math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


def _pick_mmr_candidate(
    candidates: Sequence[tuple[float, ProjectMemory, tuple[str, ...]]],
    selected: Sequence[ProjectMemory],
) -> tuple[float, ProjectMemory, tuple[str, ...]]:
    if not selected:
        return candidates[0]
    best = candidates[0]
    best_value = float("-inf")
    for candidate in candidates:
        relevance, memory, _ = candidate
        redundancy = max((_text_similarity(memory.text, item.text) for item in selected), default=0.0)
        value = MMR_LAMBDA * relevance - (1.0 - MMR_LAMBDA) * redundancy
        if value > best_value or (value == best_value and str(memory.id) < str(best[1].id)):
            best = candidate
            best_value = value
    return best


def _text_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(_tokens(left)), set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _detect_conflicts(memories: Sequence[ProjectMemory]) -> tuple[str, ...]:
    conflicts: list[str] = []
    for index, left in enumerate(memories):
        for right in memories[index + 1 :]:
            if left.memory_type != right.memory_type:
                continue
            similarity = _text_similarity(left.text, right.text)
            if 0.25 <= similarity < 0.85 and _has_conflict_marker(left.text, right.text):
                conflicts.append(f"{left.id}:{right.id}")
    return tuple(conflicts[:8])


def _has_conflict_marker(left: str, right: str) -> bool:
    markers = (
        "必须",
        "禁止",
        "只能",
        "不应",
        "允许",
        "不允许",
        "可以",
        "不可",
        "never",
        "must",
        "should",
    )
    left_lower, right_lower = left.casefold(), right.casefold()
    return any(marker in left_lower and marker not in right_lower for marker in markers) or any(
        marker in right_lower and marker not in left_lower for marker in markers
    )
