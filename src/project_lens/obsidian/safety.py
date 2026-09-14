"""Fail-closed path and content checks for the local Vault boundary."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath

from project_lens.obsidian.errors import VaultConfigurationError
from project_lens.obsidian.models import VaultConfig


class SafetyViolationError(VaultConfigurationError):
    """A path or payload violates the Vault export policy."""


_FORBIDDEN_NAMES = {
    ".env",
    ".git",
    "credentials.json",
    "secrets.json",
    "project_lens.db",
    "projectlens.db",
}
_FORBIDDEN_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|cookie)\s*[:=]\s*[^\s]+"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,})\b"),
)


def assert_safe_path(config: VaultConfig, relative_path: str) -> str:
    if not relative_path or "\x00" in relative_path:
        raise SafetyViolationError("Vault path is empty or contains a NUL byte")
    normalized = relative_path.replace("\\", "/")
    windows_path = PureWindowsPath(relative_path)
    if windows_path.is_absolute() or windows_path.drive or normalized.startswith("/"):
        raise SafetyViolationError("absolute and UNC Vault paths are forbidden")
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise SafetyViolationError("Vault paths must not contain traversal segments")
    if not parts or parts[0] not in config.directory_names():
        raise SafetyViolationError("Vault writes are limited to controlled top-level directories")
    for part in parts:
        lowered = part.casefold()
        if lowered in _FORBIDDEN_NAMES or lowered.endswith(_FORBIDDEN_SUFFIXES):
            raise SafetyViolationError(f"forbidden Vault path component: {part}")
    return "/".join(parts)


def assert_safe_content(content: str, *, max_file_bytes: int) -> None:
    if len(content.encode("utf-8")) > max_file_bytes:
        raise SafetyViolationError("Vault file exceeds the configured byte limit")
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        raise SafetyViolationError("Vault content resembles a secret or credential")
