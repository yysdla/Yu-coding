"""Durable, atomically replaced export manifest for one Vault."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import ensure_vault_layout, resolve_vault_path


class VaultManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relative_path: str = Field(min_length=1, max_length=1_000)
    content_hash: str = Field(min_length=16, max_length=128)
    source_keys: tuple[str, ...] = ()
    status: str = Field(min_length=1, max_length=80)
    exported_at: datetime


class VaultManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    tenant_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    last_export_id: str | None = None
    last_success_at: datetime | None = None
    entries: tuple[VaultManifestEntry, ...] = ()
    failed_safety_scans: tuple[str, ...] = ()


class ManifestStore:
    """Read and atomically replace `90-system/manifest.json`."""

    CURRENT_SCHEMA_VERSION = 1
    MANIFEST_PATH = "90-system/manifest.json"

    def __init__(self, config: VaultConfig) -> None:
        self._config = config

    @property
    def path(self) -> Path:
        return resolve_vault_path(self._config, self.MANIFEST_PATH)

    def load(self) -> VaultManifest | None:
        path = self.path
        if not path.exists():
            return None
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            manifest = VaultManifest.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ObsidianError(f"cannot read Vault manifest: {path}") from exc
        if manifest.schema_version > self.CURRENT_SCHEMA_VERSION:
            raise ObsidianError("Vault manifest schema is newer than this runtime")
        if manifest.tenant_id != self._config.tenant_id or manifest.project_id != self._config.project_id:
            raise ObsidianError("Vault manifest project scope does not match configuration")
        return manifest

    def load_or_create(self) -> VaultManifest:
        return self.load() or VaultManifest(
            tenant_id=self._config.tenant_id,
            project_id=self._config.project_id,
        )

    def save(self, manifest: VaultManifest) -> None:
        if manifest.tenant_id != self._config.tenant_id or manifest.project_id != self._config.project_id:
            raise ObsidianError("cannot save a manifest for another project")
        if manifest.schema_version != self.CURRENT_SCHEMA_VERSION:
            raise ObsidianError("unsupported Vault manifest schema version")
        ensure_vault_layout(self._config)
        path = self.path
        temp = path.with_name(f".{path.name}.tmp")
        payload = manifest.model_dump(mode="json")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
