"""ACL-filtered, read-only export of ProjectLens knowledge into Obsidian."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from project_lens.application.wiki_compiler import WikiDraftStore, WikiPageDraft
from project_lens.context.authority import detect_gaps
from project_lens.context.models import AccessContext
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.context.source_store import SourceRecordStore
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.frontmatter import serialize_frontmatter
from project_lens.obsidian.manifest import ManifestStore, VaultManifest, VaultManifestEntry
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import ensure_vault_layout, resolve_vault_path
from project_lens.obsidian.safety import assert_safe_content


@dataclass(frozen=True)
class ObsidianExportResult:
    project: ProjectRef
    export_id: str
    exported_paths: tuple[str, ...]
    skipped_paths: tuple[str, ...]
    source_count: int
    wiki_count: int
    review_count: int
    generated_at: datetime
    conflicted_paths: tuple[str, ...] = ()


class ObsidianExportService:
    """Write only ProjectLens-managed, ACL-filtered content into one Vault."""

    def __init__(
        self,
        *,
        source_store: SourceRecordStore,
        wiki_store: WikiDraftStore,
        config_for_project,
    ) -> None:
        self._sources = source_store
        self._wiki = wiki_store
        self._config_for_project = config_for_project

    def export_project(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        include_sources: bool = True,
        include_review: bool = True,
        now: datetime | None = None,
    ) -> ObsidianExportResult:
        if access.tenant_id != project.tenant_id:
            raise ObsidianError("access tenant does not match export project")
        config: VaultConfig = self._config_for_project(project)
        if not config.enabled:
            raise ObsidianError("Obsidian export is disabled")
        generated_at = now or datetime.now(timezone.utc)
        records = tuple(
            item
            for item in self._sources.all(
                tenant_id=project.tenant_id,
                project_id=project.project_id,
            )
            if item.access_scope in access.permissions
        )
        drafts = tuple(
            item
            for item in self._wiki.list_for_project(project.tenant_id, project.project_id)
            if item.tenant_id == project.tenant_id and item.project_id == project.project_id
        )
        documents: list[_ExportDocument] = []
        if include_sources:
            documents.extend(_source_documents(records))
        documents.extend(_wiki_documents(drafts))
        documents.extend(
            _navigation_documents(project, drafts, records, generated_at, include_review=include_review)
        )
        if include_review:
            documents.extend(_review_documents(project, records, generated_at))
        return self._write_documents(
            config=config,
            project=project,
            documents=tuple(documents),
            generated_at=generated_at,
        )

    def _write_documents(
        self,
        *,
        config: VaultConfig,
        project: ProjectRef,
        documents: tuple[_ExportDocument, ...],
        generated_at: datetime,
    ) -> ObsidianExportResult:
        ensure_vault_layout(config)
        store = ManifestStore(config)
        previous = store.load_or_create()
        previous_by_path = {item.relative_path: item for item in previous.entries}
        entries: list[VaultManifestEntry] = []
        exported: list[str] = []
        skipped: list[str] = []
        conflicted: list[str] = []
        backups: list[tuple[Path, bytes | None]] = []
        export_id = _export_id(project, generated_at, documents)
        try:
            for document in documents:
                assert_safe_content(document.content, max_file_bytes=config.max_file_bytes)
                target = resolve_vault_path(config, document.relative_path)
                content_hash = _hash(document.content)
                old = previous_by_path.get(document.relative_path)
                if target.exists():
                    current_hash = _file_hash(target)
                    if old is None or current_hash != old.content_hash:
                        conflicted.append(document.relative_path)
                        if old is not None:
                            entries.append(old)
                        continue
                if old is not None and old.content_hash == content_hash and target.exists():
                    skipped.append(document.relative_path)
                else:
                    backups.append((target, target.read_bytes() if target.exists() else None))
                    _atomic_write(target, document.content)
                    exported.append(document.relative_path)
                entries.append(
                    VaultManifestEntry(
                        relative_path=document.relative_path,
                        content_hash=content_hash,
                        source_keys=document.source_keys,
                        status=document.status,
                        exported_at=generated_at,
                    )
                )
        except Exception:
            for target, previous_bytes in reversed(backups):
                _restore_bytes(target, previous_bytes)
            raise
        manifest = VaultManifest(
            schema_version=ManifestStore.CURRENT_SCHEMA_VERSION,
            tenant_id=project.tenant_id,
            project_id=project.project_id,
            last_export_id=export_id,
            last_success_at=generated_at,
            entries=tuple(sorted(entries, key=lambda item: item.relative_path)),
            failed_safety_scans=tuple(conflicted),
        )
        store.save(manifest)
        source_count = sum(
            1
            for item in documents
            if item.kind == "source" and ".current." not in item.relative_path
        )
        wiki_count = sum(1 for item in documents if item.kind in {"wiki", "home"})
        review_count = sum(1 for item in documents if item.kind == "review")
        return ObsidianExportResult(
            project=project,
            export_id=export_id,
            exported_paths=tuple(exported),
            skipped_paths=tuple(skipped),
            source_count=source_count,
            wiki_count=wiki_count,
            review_count=review_count,
            generated_at=generated_at,
            conflicted_paths=tuple(conflicted),
        )


@dataclass(frozen=True)
class _ExportDocument:
    relative_path: str
    content: str
    source_keys: tuple[str, ...]
    status: str
    kind: str


def _source_documents(records: tuple[SourceRecord, ...]) -> tuple[_ExportDocument, ...]:
    documents: list[_ExportDocument] = []
    for record in records:
        source_key = ":".join(record.key)
        relative_path = (
            f"10-sources/{_source_directory(record.source_type)}/"
            f"{_safe_slug(record.title or record.source_id)}--{_safe_slug(record.revision)}.md"
        )
        metadata = {
            "tenant_id": record.tenant_id,
            "project_id": record.project_id,
            "page_type": "source_mirror",
            "status": record.status,
            "derived": False,
            "immutable": True,
            "source_type": record.source_type.value,
            "source_id": record.source_id,
            "revision": record.revision,
            "observed_at": record.observed_at.isoformat(),
            "raw_uri": record.raw_uri,
            "access_scope": record.access_scope,
            "source_key": source_key,
            "authority_scope": list(record.authority_scope),
        }
        documents.append(
            _ExportDocument(
                relative_path=relative_path,
                content=f"{serialize_frontmatter(metadata)}\n\n{record.content}\n",
                source_keys=(source_key,),
                status=record.status,
                kind="source",
            )
        )
        if record.source_type is SourceType.FEISHU_BITABLE:
            documents.extend(_bitable_current_documents(record, source_key))
    return tuple(documents)


def _bitable_current_documents(record: SourceRecord, source_key: str) -> tuple[_ExportDocument, ...]:
    name = _safe_slug(record.source_id)
    metadata = {
        "tenant_id": record.tenant_id,
        "project_id": record.project_id,
        "page_type": "source_current_view",
        "status": record.status,
        "derived": False,
        "source_key": source_key,
        "revision": record.revision,
    }
    markdown = _ExportDocument(
        relative_path=f"10-sources/feishu-bitable/{name}.current.md",
        content=f"{serialize_frontmatter(metadata)}\n\n{record.content}\n",
        source_keys=(source_key,),
        status=record.status,
        kind="source",
    )
    try:
        parsed = json.loads(record.content)
    except json.JSONDecodeError:
        return (markdown,)
    payload = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return (
        markdown,
        _ExportDocument(
            relative_path=f"10-sources/feishu-bitable/{name}.current.json",
            content=payload,
            source_keys=(source_key,),
            status=record.status,
            kind="source",
        ),
    )


def _wiki_documents(drafts: tuple[WikiPageDraft, ...]) -> tuple[_ExportDocument, ...]:
    return tuple(
        _ExportDocument(
            relative_path=(
                f"20-wiki/{draft.page_type.value}/"
                f"{_safe_slug(draft.title or draft.page_type.value)}.md"
            ),
            content=(
                f"{serialize_frontmatter({
                    'tenant_id': draft.tenant_id,
                    'project_id': draft.project_id,
                    'page_type': draft.page_type.value,
                    'status': draft.status,
                    'derived': True,
                    'generated_by': 'projectlens',
                    'generated_at': draft.generated_at.isoformat(),
                    'source_keys': list(draft.source_keys),
                })}\n\n{draft.content}\n"
            ),
            source_keys=draft.source_keys,
            status=draft.status,
            kind="wiki",
        )
        for draft in drafts
    )


def _navigation_documents(
    project: ProjectRef,
    drafts: tuple[WikiPageDraft, ...],
    records: tuple[SourceRecord, ...],
    generated_at: datetime,
    *,
    include_review: bool,
) -> tuple[_ExportDocument, ...]:
    source_keys = tuple(":".join(item.key) for item in records)
    links = [
        f"- [[../20-wiki/{draft.page_type.value}/{_safe_slug(draft.title or draft.page_type.value)}|{draft.title}]]"
        for draft in sorted(drafts, key=lambda item: item.page_type.value)
    ]
    home = "\n".join(
        [
            f"{serialize_frontmatter(_derived_metadata(project, 'home', 'draft', source_keys, generated_at))}",
            "",
            f"# {project.project_id}",
            "",
            "## Wiki",
            *(links or ["- 当前没有可导出的 Wiki 页面。"]),
            "",
            *(
                ["## Review", "- [[../50-review/Knowledge-Gaps|Knowledge Gaps]]"]
                if include_review
                else []
            ),
        ]
    ) + "\n"
    overview = "\n".join(
        [
            f"{serialize_frontmatter(_derived_metadata(project, 'project_overview', 'draft', source_keys, generated_at))}",
            "",
            "# Project Overview",
            "",
            f"- project: `{project.tenant_id}/{project.project_id}`",
            f"- source revisions: {len(records)}",
            f"- wiki pages: {len(drafts)}",
        ]
    ) + "\n"
    return (
        _ExportDocument("00-home/Home.md", home, source_keys, "draft", "home"),
        _ExportDocument("00-home/Project-Overview.md", overview, source_keys, "draft", "home"),
    )


def _review_documents(
    project: ProjectRef,
    records: tuple[SourceRecord, ...],
    generated_at: datetime,
) -> tuple[_ExportDocument, ...]:
    gaps = detect_gaps(records, project_id=project.project_id, now=generated_at)
    documents: list[_ExportDocument] = []
    gap_lines = ["# Knowledge Gaps", ""]
    for gap in gaps:
        gap_lines.append(f"- {gap.status}: {gap.reason}")
    if not gaps:
        gap_lines.append("- No detected knowledge gaps.")
    documents.append(
        _ExportDocument(
            relative_path="50-review/Knowledge-Gaps.md",
            content=(
                f"{serialize_frontmatter(_derived_metadata(project, 'knowledge_gaps', 'review_required', (), generated_at))}\n\n"
                + "\n".join(gap_lines)
                + "\n"
            ),
            source_keys=(),
            status="review_required",
            kind="review",
        )
    )
    for gap in gaps:
        if gap.status not in {"conflict", "stale", "unconfirmed"}:
            continue
        relative_path = f"50-review/{_review_directory(gap.status)}/{_safe_slug(gap.fact_type.value)}.md"
        keys = tuple(
            f"{project.tenant_id}:{project.project_id}:{evidence_id}"
            for evidence_id in gap.evidence_ids
        )
        body = f"# {gap.fact_type.value}\n\n- status: {gap.status}\n- reason: {gap.reason}\n"
        documents.append(
            _ExportDocument(
                relative_path=relative_path,
                content=(
                    f"{serialize_frontmatter(_derived_metadata(project, 'review', gap.status, keys, generated_at))}\n\n{body}"
                ),
                source_keys=keys,
                status=gap.status,
                kind="review",
            )
        )
    return tuple(documents)


def _derived_metadata(
    project: ProjectRef,
    page_type: str,
    status: str,
    source_keys: tuple[str, ...],
    generated_at: datetime,
) -> dict[str, object]:
    return {
        "tenant_id": project.tenant_id,
        "project_id": project.project_id,
        "page_type": page_type,
        "status": status,
        "derived": True,
        "generated_by": "projectlens",
        "generated_at": generated_at.isoformat(),
        "source_keys": list(source_keys),
    }


def _source_directory(source_type: SourceType) -> str:
    if source_type is SourceType.FEISHU_DOCUMENT:
        return "feishu-docs"
    if source_type is SourceType.FEISHU_BITABLE:
        return "feishu-bitable"
    if source_type is SourceType.MEETING_MINUTE:
        return "meetings"
    if source_type in {
        SourceType.GITHUB_ISSUE,
        SourceType.GITHUB_PULL_REQUEST,
        SourceType.GITHUB_COMMIT,
        SourceType.GITHUB_CODE,
    }:
        return "github"
    return "other"


def _review_directory(status: str) -> str:
    return {"conflict": "conflicts", "stale": "stale-pages"}.get(status, "pending-approval")


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff ._-]+", "-", value).strip(" .-")
    return cleaned[:120] or "untitled"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)


def _restore_bytes(path: Path, content: bytes | None) -> None:
    if content is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.restore.tmp")
    temp.write_bytes(content)
    os.replace(temp, path)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    # Compare logical Markdown content so Windows CRLF normalization does not
    # turn an unchanged generated page into a false manual-edit conflict.
    return _hash(path.read_text(encoding="utf-8"))


def _export_id(project: ProjectRef, generated_at: datetime, documents: tuple[_ExportDocument, ...]) -> str:
    digest = hashlib.sha256()
    digest.update(f"{project.tenant_id}:{project.project_id}:{generated_at.isoformat()}".encode())
    for document in documents:
        digest.update(document.relative_path.encode())
        digest.update(_hash(document.content).encode())
    return digest.hexdigest()[:24]
