"""Controlled filesystem operations for a Vault."""

from __future__ import annotations

from pathlib import Path

from project_lens.obsidian.errors import VaultConfigurationError
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.safety import assert_safe_path


def validate_vault_root(config: VaultConfig) -> Path:
    root = config.resolved_root()
    allowed = config.resolved_allowed_root()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise VaultConfigurationError("Vault root must be inside the configured allowed root") from exc
    return root


def resolve_vault_path(config: VaultConfig, relative_path: str) -> Path:
    normalized = assert_safe_path(config, relative_path)
    root = validate_vault_root(config)
    target = (root / Path(normalized)).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise VaultConfigurationError("resolved Vault path escaped the Vault root") from exc
    return target


def ensure_vault_layout(config: VaultConfig) -> Path:
    root = validate_vault_root(config)
    root.mkdir(parents=True, exist_ok=True)
    for directory in config.directory_names():
        (root / directory).mkdir(parents=True, exist_ok=True)
    return root
