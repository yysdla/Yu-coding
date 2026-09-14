"""Manifest-bounded read access to derived Obsidian Wiki pages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.frontmatter import parse_frontmatter
from project_lens.obsidian.manifest import ManifestStore
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import resolve_vault_path


@dataclass(frozen=True)
class WikiPageSummary:
    path: str
    title: str
    page_type: str
    status: str
    snippet: str
    source_keys: tuple[str, ...]
    generated_at: str | None


@dataclass(frozen=True)
class WikiPage:
    summary: WikiPageSummary
    content: str
    derived: bool
    truncated: bool
    warnings: tuple[str, ...] = ()


class ObsidianRepository:
    """Read only manifest-registered derived pages for one project."""

    _ALLOWED_PREFIXES = ("00-home/", "20-wiki/", "50-review/")

    def __init__(self, *, config_for_project, max_page_chars: int = 12_000) -> None:
        self._config_for_project = config_for_project
        self._max_page_chars = max(1_000, min(max_page_chars, 50_000))

    def search(
        self,
        *,
        project,
        query: str,
        page_types: tuple[str, ...] = (),
        statuses: tuple[str, ...] = (),
        limit: int = 8,
    ) -> tuple[WikiPageSummary, ...]:
        config = self._config_for_project(project)
        manifest = self._manifest(config)
        query_tokens = _tokens(query)
        page_type_filter = {item.strip().casefold() for item in page_types if item.strip()}
        status_filter = {item.strip().casefold() for item in statuses if item.strip()}
        scored: list[tuple[int, WikiPageSummary]] = []
        for entry in manifest.entries:
            if not self._is_allowed_page(entry.relative_path):
                continue
            page = self._read_registered(config, entry.relative_path)
            if page_type_filter and page.summary.page_type.casefold() not in page_type_filter:
                continue
            if status_filter and page.summary.status.casefold() not in status_filter:
                continue
            haystack = " ".join(
                (page.summary.title, page.summary.page_type, page.summary.status, page.content)
            ).casefold()
            score = sum(1 for token in query_tokens if token in haystack)
            if query_tokens and score == 0:
                continue
            scored.append((score, page.summary))
        scored.sort(key=lambda item: (-item[0], item[1].path))
        bounded = max(1, min(int(limit), 8))
        return tuple(item[1] for item in scored[:bounded])

    def read(self, *, project, relative_path: str, max_chars: int | None = None) -> WikiPage:
        config = self._config_for_project(project)
        manifest = self._manifest(config)
        normalized = relative_path.replace("\\", "/")
        registered = {entry.relative_path for entry in manifest.entries}
        if normalized not in registered or not self._is_allowed_page(normalized):
            raise PermissionError("Wiki page is not registered or is outside the derived page allowlist")
        page = self._read_registered(config, normalized)
        budget = max(1_000, min(int(max_chars or self._max_page_chars), self._max_page_chars))
        if len(page.content) <= budget:
            return page
        return WikiPage(
            summary=page.summary,
            content=page.content[:budget],
            derived=page.derived,
            truncated=True,
            warnings=("page content was truncated to the configured character budget",),
        )

    def _manifest(self, config: VaultConfig):
        if not config.enabled:
            raise ObsidianError("Obsidian Wiki is disabled")
        manifest = ManifestStore(config).load()
        if manifest is None:
            raise ObsidianError("Obsidian Vault manifest is not available")
        return manifest

    def _read_registered(self, config: VaultConfig, relative_path: str) -> WikiPage:
        path = resolve_vault_path(config, relative_path)
        try:
            document = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ObsidianError(f"cannot read registered Wiki page: {relative_path}") from exc
        metadata, body = parse_frontmatter(document)
        if metadata.get("tenant_id") != config.tenant_id or metadata.get("project_id") != config.project_id:
            raise PermissionError("Wiki page project scope does not match the configured Vault")
        if metadata.get("derived") is not True:
            raise PermissionError("only derived Wiki pages may be read through the Wiki repository")
        source_keys = tuple(str(item) for item in metadata.get("source_keys", ()))
        page_type = str(metadata.get("page_type") or "unknown")
        status = str(metadata.get("status") or "unknown")
        title = _title(metadata, body, relative_path)
        return WikiPage(
            summary=WikiPageSummary(
                path=relative_path,
                title=title,
                page_type=page_type,
                status=status,
                snippet=_snippet(body),
                source_keys=source_keys,
                generated_at=(str(metadata["generated_at"]) if metadata.get("generated_at") else None),
            ),
            content=body,
            derived=True,
            truncated=False,
        )

    @classmethod
    def _is_allowed_page(cls, relative_path: str) -> bool:
        return relative_path.endswith(".md") and relative_path.startswith(cls._ALLOWED_PREFIXES)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9_./:-]+|[\u4e00-\u9fff]", value.casefold()))


def _title(metadata: dict[str, object], body: str, relative_path: str) -> str:
    if metadata.get("title"):
        return str(metadata["title"])
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(relative_path).stem


def _snippet(body: str) -> str:
    return " ".join(line.strip() for line in body.splitlines() if line.strip())[:500]
