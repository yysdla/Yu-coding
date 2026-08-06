"""Structured patch plans for Project Engineering Validate stage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FilePatch:
    path: str
    old_text: str
    new_text: str


@dataclass(frozen=True)
class PatchPlan:
    title: str
    rationale: str
    patches: tuple[FilePatch, ...]
    test_commands: tuple[str, ...] = ("pytest -q",)

    def affected_paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.patches)
