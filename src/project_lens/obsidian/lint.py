"""Read-only diagnostics for ProjectLens-managed Obsidian Vaults."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from project_lens.context.source_store import SourceRecordStore
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.frontmatter import parse_frontmatter, serialize_frontmatter
from project_lens.obsidian.manifest import ManifestStore, VaultManifestEntry
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import resolve_vault_path
from project_lens.obsidian.safety import assert_safe_content, assert_safe_path


@dataclass(frozen=True)
class LintFinding:
    code: str
    path: str
    message: str
    severity: str = "warning"


class ObsidianLintService:
    """Validate generated pages without changing their source or Wiki content."""

    def __init__(self, *, source_store: SourceRecordStore, config_for_project) -> None:
        self._sources = source_store
        self._config_for_project = config_for_project

    def lint(self, *, project: ProjectRef) -> tuple[LintFinding, ...]:
        config: VaultConfig = self._config_for_project(project)
        if not config.enabled:
            raise ObsidianError("Obsidian lint is disabled")
        manifest = ManifestStore(config).load()
        if manifest is None:
            raise ObsidianError("Obsidian Vault manifest is not available")
        known_source_keys = {
            ":".join(item.key)
            for item in self._sources.all(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
                include_revoked=True,
            )
        }
        findings: list[LintFinding] = []
        seen_paths: set[str] = set()
        for entry in manifest.entries:
            if entry.relative_path in seen_paths:
                findings.append(LintFinding("duplicate_manifest_path", entry.relative_path, "manifest contains duplicate path", "error"))
                continue
            seen_paths.add(entry.relative_path)
            findings.extend(self._lint_entry(config, project, entry, known_source_keys))
        findings.extend(self._scan_shared_files(config))
        return tuple(findings)

    def write_review(self, *, project: ProjectRef, findings: tuple[LintFinding, ...], now: datetime | None = None) -> str:
        config: VaultConfig = self._config_for_project(project)
        generated_at = now or datetime.now(timezone.utc)
        path = "50-review/Lint-Findings.md"
        lines = ["# Lint Findings", ""]
        if findings:
            lines.extend(f"- [{item.severity}] `{item.code}` {item.path}: {item.message}" for item in findings)
        else:
            lines.append("- No lint findings.")
        content = (
            f"{serialize_frontmatter({
                'tenant_id': project.tenant_id,
                'project_id': project.project_id,
                'page_type': 'lint_findings',
                'status': 'review_required' if findings else 'draft',
                'derived': True,
                'generated_by': 'projectlens',
                'generated_at': generated_at.isoformat(),
                'source_keys': [],
            })}\n\n" + "\n".join(lines) + "\n"
        )
        assert_safe_content(content, max_file_bytes=config.max_file_bytes)
        target = resolve_vault_path(config, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.tmp")
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
        store = ManifestStore(config)
        manifest = store.load_or_create()
        entry = VaultManifestEntry(
            relative_path=path,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            status="review_required" if findings else "draft",
            exported_at=generated_at,
        )
        entries = {item.relative_path: item for item in manifest.entries}
        entries[path] = entry
        store.save(manifest.model_copy(update={"entries": tuple(sorted(entries.values(), key=lambda item: item.relative_path))}))
        return path

    def _lint_entry(
        self,
        config: VaultConfig,
        project: ProjectRef,
        entry: VaultManifestEntry,
        known_source_keys: set[str],
    ) -> list[LintFinding]:
        findings: list[LintFinding] = []
        try:
            assert_safe_path(config, entry.relative_path)
            path = resolve_vault_path(config, entry.relative_path)
        except ObsidianError as exc:
            return [LintFinding("unsafe_path", entry.relative_path, str(exc), "error")]
        if not path.exists():
            return [LintFinding("missing_page", entry.relative_path, "manifest page does not exist", "error")]
        if path.stat().st_size > config.max_file_bytes:
            return [LintFinding("page_too_large", entry.relative_path, "page exceeds configured byte limit", "error")]
        if path.suffix != ".md":
            return findings
        try:
            metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, ObsidianError) as exc:
            return [LintFinding("invalid_frontmatter", entry.relative_path, str(exc), "error")]
        if metadata.get("tenant_id") != project.tenant_id or metadata.get("project_id") != project.project_id:
            findings.append(LintFinding("project_scope_mismatch", entry.relative_path, "frontmatter project scope does not match manifest", "error"))
        page_type = str(metadata.get("page_type") or "")
        status = str(metadata.get("status") or "")
        if not page_type or not status:
            findings.append(LintFinding("missing_page_metadata", entry.relative_path, "page_type and status are required", "error"))
        if entry.relative_path.startswith("20-wiki/") and metadata.get("derived") is not True:
            findings.append(LintFinding("wiki_not_derived", entry.relative_path, "Wiki pages must be marked derived", "error"))
        source_keys = tuple(str(item) for item in metadata.get("source_keys", ()))
        if entry.relative_path.startswith("20-wiki/") and not source_keys:
            findings.append(LintFinding("wiki_missing_source_keys", entry.relative_path, "Wiki page has no source keys", "warning"))
        for source_key in source_keys:
            if source_key not in known_source_keys:
                findings.append(LintFinding("unknown_source_key", entry.relative_path, f"source key is not present: {source_key}", "warning"))
        findings.extend(_lint_wikilinks(entry.relative_path, body, {item.relative_path for item in ManifestStore(config).load_or_create().entries}))
        return findings

    def _scan_shared_files(self, config: VaultConfig) -> list[LintFinding]:
        root = config.resolved_root()
        allowed_suffixes = {".md", ".json"}
        findings: list[LintFinding] = []
        if not root.exists():
            return findings
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if path.suffix.casefold() not in allowed_suffixes:
                findings.append(LintFinding("forbidden_file_type", relative, "file type is not allowed in the shared Vault", "error"))
        return findings


def _lint_wikilinks(path: str, body: str, manifest_paths: set[str]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for raw_target in re.findall(r"\[\[([^]|]+)(?:\|[^]]+)?\]\]", body):
        target = raw_target.strip()
        if not target or target.startswith("http"):
            continue
        normalized = target.replace("\\", "/")
        if normalized.endswith(".md") and normalized not in manifest_paths:
            findings.append(LintFinding("missing_wikilink_target", path, f"linked page is not manifest-registered: {target}", "warning"))
    return findings
