"""Pydantic contracts for a ProjectLens-owned Obsidian Vault."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VaultConfig(BaseModel):
    """Resolved configuration for one tenant/project Vault."""

    model_config = ConfigDict(extra="forbid")

    root: Path
    tenant_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    allowed_root: Path | None = None
    enabled: bool = False
    project_subdir: str = Field(default="projectlens", min_length=1, max_length=100)
    export_mode: str = Field(default="reviewed", min_length=1, max_length=40)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=50_000_000)
    git_enabled: bool = False
    source_dir: str = "10-sources"
    wiki_dir: str = "20-wiki"
    memory_dir: str = "30-memory"
    inbox_dir: str = "40-inbox"
    review_dir: str = "50-review"
    system_dir: str = "90-system"

    @field_validator(
        "source_dir", "wiki_dir", "memory_dir", "inbox_dir", "review_dir", "system_dir"
    )
    @classmethod
    def directory_names_are_relative(cls, value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("Vault directory names must be single relative path components")
        return value

    def resolved_root(self) -> Path:
        return self.root.expanduser().resolve(strict=False)

    def resolved_allowed_root(self) -> Path:
        if self.allowed_root is None:
            raise ValueError("allowed_root is required for an enabled Obsidian Vault")
        return self.allowed_root.expanduser().resolve(strict=False)

    def directory_names(self) -> tuple[str, ...]:
        return (
            "00-home",
            self.source_dir,
            self.wiki_dir,
            self.memory_dir,
            self.inbox_dir,
            self.review_dir,
            self.system_dir,
        )
