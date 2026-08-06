"""Evidence index ports and an in-memory implementation for the first vertical slice."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from project_lens.domain.models import Evidence


class EvidenceIndex(Protocol):
    def add_many(self, evidence: Iterable[Evidence]) -> int: ...

    def all(self) -> Sequence[Evidence]: ...


class InMemoryEvidenceIndex:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], Evidence] = {}

    def add_many(self, evidence: Iterable[Evidence]) -> int:
        added = 0
        for item in evidence:
            key = (item.source.system, item.source.source_id, item.content_hash)
            if key not in self._items:
                added += 1
            self._items[key] = item
        return added

    def remove_source_prefix(self, *, system: str, source_id_prefix: str) -> int:
        """Remove indexed chunks whose source_id starts with the given prefix."""

        to_delete = [
            key
            for key, item in self._items.items()
            if item.source.system == system and item.source.source_id.startswith(source_id_prefix)
        ]
        for key in to_delete:
            del self._items[key]
        return len(to_delete)

    def all(self) -> tuple[Evidence, ...]:
        return tuple(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

