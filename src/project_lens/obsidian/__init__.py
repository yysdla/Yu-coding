"""Secure local Obsidian Vault contracts and adapters."""

from project_lens.obsidian.frontmatter import parse_frontmatter, serialize_frontmatter
from project_lens.obsidian.manifest import ManifestStore, VaultManifest, VaultManifestEntry
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import ensure_vault_layout, resolve_vault_path
from project_lens.obsidian.safety import SafetyViolationError, assert_safe_content, assert_safe_path
from project_lens.obsidian.exporter import ObsidianExportResult, ObsidianExportService
from project_lens.obsidian.repository import ObsidianRepository, WikiPage, WikiPageSummary
from project_lens.obsidian.inbox import InboxCandidate, ObsidianInboxScanner
from project_lens.obsidian.git import (
    ObsidianGitCommit,
    ObsidianGitDiff,
    ObsidianGitService,
    ObsidianGitStatus,
    ObsidianRecoveryReport,
)

__all__ = [
    "ManifestStore",
    "ObsidianExportResult",
    "ObsidianExportService",
    "ObsidianRepository",
    "ObsidianInboxScanner",
    "SafetyViolationError",
    "VaultConfig",
    "VaultManifest",
    "VaultManifestEntry",
    "WikiPage",
    "WikiPageSummary",
    "InboxCandidate",
    "ObsidianGitCommit",
    "ObsidianGitDiff",
    "ObsidianGitService",
    "ObsidianGitStatus",
    "ObsidianRecoveryReport",
    "assert_safe_content",
    "assert_safe_path",
    "ensure_vault_layout",
    "parse_frontmatter",
    "resolve_vault_path",
    "serialize_frontmatter",
]
