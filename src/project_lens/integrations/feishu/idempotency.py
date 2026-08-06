"""Idempotency storage for Feishu event callbacks."""

from __future__ import annotations

from typing import Protocol


class EventDeduplicator(Protocol):
    def mark_seen(self, event_id: str) -> bool: ...


class InMemoryEventDeduplicator:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def mark_seen(self, event_id: str) -> bool:
        if event_id in self._seen:
            return False
        self._seen.add(event_id)
        return True
