"""Index local Git commits into COMMIT evidence."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from project_lens.context.indexing.common import content_hash, utc_now
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef

LogRunner = Callable[[Path, tuple[str, ...]], str]


@dataclass(frozen=True)
class ParsedCommit:
    sha: str
    author: str
    committed_at: datetime
    subject: str
    body: str
    branch: str
    files_changed: tuple[str, ...]
    insertions: int = 0
    deletions: int = 0
    parent_sha: str | None = None
    release_tag: str | None = None


class GitChangeIndexer:
    """Build COMMIT evidence from a local git repository or parsed log text."""

    def __init__(self, *, log_runner: LogRunner | None = None) -> None:
        self._log_runner = log_runner or _run_git

    def index(
        self,
        repository_root: Path,
        *,
        project: ProjectRef,
        access_scope: str,
        branch: str | None = None,
        limit: int = 50,
    ) -> list[Evidence]:
        root = repository_root.resolve()
        branch_name = branch or self._current_branch(root)
        raw = self._log_runner(
            root,
            (
                "log",
                f"-n{max(1, min(limit, 200))}",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%an%x1f%ad%x1f%P%x1f%s%x1f%b%x1e",
                "--name-only",
            ),
        )
        commits = parse_git_log(raw, branch=branch_name)
        return [self._to_evidence(item, project=project, access_scope=access_scope) for item in commits]

    def index_parsed(
        self,
        commits: list[ParsedCommit],
        *,
        project: ProjectRef,
        access_scope: str,
    ) -> list[Evidence]:
        return [self._to_evidence(item, project=project, access_scope=access_scope) for item in commits]

    def index_log_file(
        self,
        log_file: Path,
        *,
        project: ProjectRef,
        access_scope: str,
        branch: str | None = None,
        limit: int = 50,
    ) -> list[Evidence]:
        raw = log_file.read_text(encoding="utf-8")
        commits = parse_git_log(raw, branch=branch or "main")[: max(1, min(limit, 200))]
        return self.index_parsed(commits, project=project, access_scope=access_scope)

    def _current_branch(self, root: Path) -> str:
        try:
            return self._log_runner(root, ("rev-parse", "--abbrev-ref", "HEAD")).strip() or "HEAD"
        except (OSError, subprocess.CalledProcessError, ValueError):
            return "HEAD"

    def _to_evidence(
        self,
        commit: ParsedCommit,
        *,
        project: ProjectRef,
        access_scope: str,
    ) -> Evidence:
        files = ", ".join(commit.files_changed[:20]) or "(no files listed)"
        content = (
            f"commit: {commit.sha}\n"
            f"author: {commit.author}\n"
            f"branch: {commit.branch}\n"
            f"subject: {commit.subject}\n"
            f"body: {commit.body}\n"
            f"files_changed: {files}\n"
            f"insertions: {commit.insertions}\n"
            f"deletions: {commit.deletions}"
        )
        metadata: dict[str, object] = {
            "commit_sha": commit.sha,
            "author": commit.author,
            "branch": commit.branch,
            "files_changed": list(commit.files_changed),
            "insertions": commit.insertions,
            "deletions": commit.deletions,
            "title": commit.subject[:300],
        }
        if commit.parent_sha:
            metadata["parent_sha"] = commit.parent_sha
        if commit.release_tag:
            metadata["release_tag"] = commit.release_tag
        return Evidence(
            type=EvidenceType.COMMIT,
            project=project,
            source=SourceRef(system="local_git", source_id=commit.sha, url=None),
            content=content,
            observed_at=commit.committed_at,
            access_scope=access_scope,
            content_hash=content_hash(content),
            metadata=metadata,
        )


def parse_git_log(raw: str, *, branch: str = "HEAD") -> list[ParsedCommit]:
    """Parse `git log --name-only` output where records end with ``\\x1e``.

    Git emits: ``<header>\\x1e\\nfile1\\nfile2\\n\\n<header>\\x1e...``, so file
    lines for commit N appear at the start of the split segment for N+1.
    """
    commits: list[ParsedCommit] = []
    pending_files: list[str] = []
    for block in raw.split("\x1e"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        header_index = next((index for index, line in enumerate(lines) if "\x1f" in line), None)
        if header_index is None:
            pending_files.extend(lines)
            continue
        leading_files = lines[:header_index]
        if commits and leading_files:
            previous = commits[-1]
            commits[-1] = ParsedCommit(
                sha=previous.sha,
                author=previous.author,
                committed_at=previous.committed_at,
                subject=previous.subject,
                body=previous.body,
                branch=previous.branch,
                files_changed=tuple(dict.fromkeys([*previous.files_changed, *leading_files])),
                insertions=previous.insertions,
                deletions=previous.deletions,
                parent_sha=previous.parent_sha,
                release_tag=previous.release_tag,
            )
        elif leading_files and not commits:
            pending_files.extend(leading_files)
        header = lines[header_index]
        parts = header.split("\x1f")
        if len(parts) < 5:
            continue
        sha, author, date_text, parents, subject = parts[:5]
        body = parts[5] if len(parts) > 5 else ""
        trailing_files = lines[header_index + 1 :]
        parent_sha = parents.split()[0] if parents.strip() else None
        files = tuple(dict.fromkeys([*pending_files, *trailing_files]))
        pending_files = []
        commits.append(
            ParsedCommit(
                sha=sha.strip(),
                author=author.strip() or "unknown",
                committed_at=_parse_commit_date(date_text),
                subject=subject.strip() or sha[:8],
                body=body.strip(),
                branch=branch,
                files_changed=files,
                parent_sha=parent_sha,
            )
        )
    if commits and pending_files:
        previous = commits[-1]
        commits[-1] = ParsedCommit(
            sha=previous.sha,
            author=previous.author,
            committed_at=previous.committed_at,
            subject=previous.subject,
            body=previous.body,
            branch=previous.branch,
            files_changed=tuple(dict.fromkeys([*previous.files_changed, *pending_files])),
            insertions=previous.insertions,
            deletions=previous.deletions,
            parent_sha=previous.parent_sha,
            release_tag=previous.release_tag,
        )
    return commits


def _parse_commit_date(value: str) -> datetime:
    text = value.strip()
    if not text:
        return utc_now()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return utc_now()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _run_git(root: Path, args: tuple[str, ...]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout
