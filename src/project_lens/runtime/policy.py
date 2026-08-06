"""Engineering tool policy: path allowlists, risk class, and write gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RiskClass(StrEnum):
    READ = "read"
    VALIDATE = "validate"
    APPLY = "apply"


@dataclass(frozen=True)
class EngineeringPolicy:
    project_root: Path
    allowed_path_prefixes: tuple[str, ...] = ("src/", "tests/")
    allowed_tests: tuple[str, ...] = ("pytest",)
    allow_apply: bool = False

    def resolve_path(self, relative_path: str) -> Path:
        cleaned = relative_path.replace("\\", "/").lstrip("./")
        if ".." in Path(cleaned).parts:
            raise PermissionError(f"path escapes project root: {relative_path}")
        if not any(cleaned == prefix.rstrip("/") or cleaned.startswith(prefix) for prefix in self.allowed_path_prefixes):
            raise PermissionError(f"path not allowed: {relative_path}")
        resolved = (self.project_root / cleaned).resolve()
        if not str(resolved).startswith(str(self.project_root.resolve())):
            raise PermissionError(f"path escapes project root: {relative_path}")
        return resolved

    def assert_can_apply(self) -> None:
        if not self.allow_apply:
            raise PermissionError("apply is disabled until explicit approval")
