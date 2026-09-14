"""Controlled scanner for human and Hermes Obsidian Inbox proposals."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from project_lens.domain.knowledge_proposal import KnowledgeProposal, KnowledgeProposalStatus
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.frontmatter import parse_frontmatter
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import resolve_vault_path
from project_lens.obsidian.frontmatter import serialize_frontmatter
from project_lens.obsidian.safety import assert_safe_content, assert_safe_path


@dataclass(frozen=True)
class InboxCandidate:
    proposal: KnowledgeProposal | None
    path: str
    status: str
    message: str


class ObsidianInboxScanner:
    """Scan only the two controlled Inbox proposal directories."""

    ALLOWED_DIRECTORIES = ("40-inbox/human-proposals", "40-inbox/hermes-proposals")

    def __init__(self, *, config_for_project) -> None:
        self._config_for_project = config_for_project

    def scan(self, *, project: ProjectRef, created_by: str) -> tuple[InboxCandidate, ...]:
        config: VaultConfig = self._config_for_project(project)
        if not config.enabled:
            raise ObsidianError("Obsidian Inbox is disabled")
        candidates: list[InboxCandidate] = []
        for directory in self.ALLOWED_DIRECTORIES:
            root = resolve_vault_path(config, directory + "/placeholder.md").parent
            if not root.exists():
                continue
            for path in sorted(root.glob("*.md")):
                relative = path.relative_to(config.resolved_root()).as_posix()
                candidates.append(self._read_candidate(config, project, path, relative, created_by))
        return tuple(candidates)

    def read_candidate(self, *, project: ProjectRef, relative_path: str, created_by: str) -> InboxCandidate:
        config: VaultConfig = self._config_for_project(project)
        normalized = assert_safe_path(config, relative_path)
        if not any(normalized.startswith(directory + "/") for directory in self.ALLOWED_DIRECTORIES):
            raise PermissionError("Inbox path is outside the controlled proposal directories")
        path = resolve_vault_path(config, normalized)
        return self._read_candidate(config, project, path, normalized, created_by)

    def validate_content(self, *, project: ProjectRef, content: str) -> None:
        config: VaultConfig = self._config_for_project(project)
        if not config.enabled:
            raise ObsidianError("Obsidian Inbox is disabled")
        assert_safe_content(content, max_file_bytes=config.max_file_bytes)

    def write_review(self, *, project: ProjectRef, proposal: KnowledgeProposal) -> str:
        config: VaultConfig = self._config_for_project(project)
        if not config.enabled:
            raise ObsidianError("Obsidian Inbox is disabled")
        relative = f"50-review/pending-approval/proposal-{proposal.id}.md"
        metadata = {
            "tenant_id": proposal.project.tenant_id,
            "project_id": proposal.project.project_id,
            "page_type": "knowledge_proposal",
            "proposal_id": proposal.id,
            "kind": proposal.kind,
            "status": proposal.status.value,
            "approval_required": True,
            "source_path": proposal.source_path,
            "source_hash": proposal.source_hash,
            "evidence_ids": list(proposal.evidence_ids),
            "created_by": proposal.created_by,
            "created_at": proposal.created_at.isoformat(),
        }
        document = (
            f"{serialize_frontmatter(metadata)}\n\n"
            f"# {proposal.title}\n\n{proposal.content}\n"
            "\n## Review\n\n- approval required: true\n"
            f"- status: {proposal.status.value}\n"
        )
        assert_safe_content(document, max_file_bytes=config.max_file_bytes)
        target = resolve_vault_path(config, relative)
        if target.exists():
            if target.read_text(encoding="utf-8") != document:
                raise ObsidianError("proposal review page already exists with different content")
            return relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(document, encoding="utf-8")
        os.replace(temporary, target)
        return relative

    def _read_candidate(
        self,
        config: VaultConfig,
        project: ProjectRef,
        path: Path,
        relative: str,
        created_by: str,
    ) -> InboxCandidate:
        if path.is_symlink() or not path.is_file():
            return InboxCandidate(None, relative, "rejected", "Inbox proposal must be a regular file")
        try:
            content = path.read_text(encoding="utf-8")
            assert_safe_content(content, max_file_bytes=config.max_file_bytes)
            metadata, body = parse_frontmatter(content)
            proposal = self._proposal_from_document(project, relative, metadata, body, created_by)
        except Exception as exc:  # noqa: BLE001 - each file becomes a bounded finding
            return InboxCandidate(None, relative, "rejected", str(exc))
        return InboxCandidate(proposal, relative, proposal.status.value, "proposal is valid")

    @staticmethod
    def _proposal_from_document(
        project: ProjectRef,
        relative: str,
        metadata: dict[str, object],
        body: str,
        created_by: str,
    ) -> KnowledgeProposal:
        required = (
            "proposal_id",
            "tenant_id",
            "project_id",
            "kind",
            "status",
            "author",
            "created_at",
            "evidence_ids",
            "approval_required",
        )
        missing = tuple(key for key in required if key not in metadata)
        if missing:
            raise ValueError(f"Inbox proposal is missing required fields: {', '.join(missing)}")
        if metadata.get("tenant_id") != project.tenant_id or metadata.get("project_id") != project.project_id:
            raise PermissionError("Inbox proposal project scope does not match the requested project")
        if str(metadata.get("status") or "proposed") != "proposed":
            raise ValueError("Inbox proposals must enter with status=proposed")
        if metadata.get("approval_required") is not True:
            raise ValueError("Inbox proposals must set approval_required=true")
        if any(key in metadata for key in ("approved_by", "confirmed", "decided_by")):
            raise ValueError("Inbox cannot carry approval or confirmed-state fields")
        title = str(metadata.get("title") or _title_from_body(body) or Path(relative).stem)
        kind = str(metadata["kind"])
        evidence_ids = _string_tuple(metadata.get("evidence_ids"))
        created_at = _datetime_value(metadata.get("created_at"))
        source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return KnowledgeProposal(
            id=str(metadata["proposal_id"]),
            project=project,
            kind=kind,
            title=title,
            content=body,
            source_path=relative,
            source_hash=source_hash,
            status=KnowledgeProposalStatus.PROPOSED,
            evidence_ids=evidence_ids,
            # The document author is validated as required metadata, but the
            # authenticated caller remains the only trusted proposal creator.
            created_by=created_by,
            created_at=created_at,
        )


def _string_tuple(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item).strip())
    raise ValueError("evidence_ids must be a string or array")


def _datetime_value(value: object) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _title_from_body(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""
