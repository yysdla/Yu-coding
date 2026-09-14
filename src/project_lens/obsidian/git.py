"""Independent, local-only Git workflow for one Obsidian Vault."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from project_lens.domain.models import ProjectRef
from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.manifest import ManifestStore, VaultManifest
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import resolve_vault_path, validate_vault_root
from project_lens.obsidian.safety import assert_safe_content, assert_safe_path


@dataclass(frozen=True)
class ObsidianGitStatus:
    project: ProjectRef
    enabled: bool
    initialized: bool
    branch: str | None = None
    clean: bool = True
    changed_paths: tuple[str, ...] = ()
    conflict_paths: tuple[str, ...] = ()
    unsafe_paths: tuple[str, ...] = ()
    ahead: int = 0
    behind: int = 0


@dataclass(frozen=True)
class ObsidianGitDiff:
    project: ProjectRef
    text: str
    paths: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class ObsidianGitCommit:
    project: ProjectRef
    committed: bool
    commit_id: str | None = None
    message: str | None = None
    changed_paths: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ObsidianRecoveryReport:
    project: ProjectRef
    manifest_status: str
    manifest_error: str | None = None
    recoverable_from_git: bool = False
    missing_paths: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()


class ObsidianGitService:
    """Run Git only inside the configured Vault; never push or merge."""

    def __init__(self, *, config_for_project, max_output_chars: int = 100_000) -> None:
        self._config_for_project = config_for_project
        self._max_output_chars = max_output_chars

    def ensure_repository(self, *, project: ProjectRef) -> ObsidianGitStatus:
        config = self._config(project)
        root = validate_vault_root(config)
        root.mkdir(parents=True, exist_ok=True)
        if self._is_initialized(root):
            return self.status(project=project)
        self._run(root, ("init", "--initial-branch=main"))
        return self.status(project=project)

    def status(self, *, project: ProjectRef) -> ObsidianGitStatus:
        config: VaultConfig = self._config_for_project(project)
        if not config.enabled or not config.git_enabled:
            return ObsidianGitStatus(project=project, enabled=False, initialized=False)
        root = validate_vault_root(config)
        if not self._is_initialized(root):
            return ObsidianGitStatus(project=project, enabled=True, initialized=False)
        completed = self._run(root, ("status", "--porcelain=v1", "--branch", "--untracked-files=all"))
        lines = completed.stdout.splitlines()
        branch = None
        changed: list[str] = []
        conflicts: list[str] = []
        unsafe: list[str] = []
        for index, line in enumerate(lines):
            if index == 0 and line.startswith("## "):
                branch = line[3:].split("...", 1)[0]
                continue
            if len(line) < 3:
                continue
            path = line[3:].split(" -> ", 1)[-1].strip().strip('"')
            try:
                normalized = assert_safe_path(config, path)
            except Exception:
                unsafe.append(path)
                continue
            changed.append(normalized)
            if "U" in line[:2] or normalized.startswith("50-review/conflicts/"):
                conflicts.append(normalized)
        ahead, behind = _ahead_behind(lines[0] if lines else "")
        return ObsidianGitStatus(
            project=project,
            enabled=True,
            initialized=True,
            branch=branch,
            clean=not changed and not unsafe,
            changed_paths=tuple(sorted(set(changed))),
            conflict_paths=tuple(sorted(set(conflicts))),
            unsafe_paths=tuple(sorted(set(unsafe))),
            ahead=ahead,
            behind=behind,
        )

    def diff(self, *, project: ProjectRef, staged: bool = False) -> ObsidianGitDiff:
        config = self._config(project)
        root = self._require_repo(config)
        args = ["diff", "--no-ext-diff"]
        if staged:
            args.append("--cached")
        args.extend(["--", *config.directory_names()])
        output = self._run(root, tuple(args)).stdout
        paths_output = self._run(
            root,
            ("diff", "--name-only", *(('--cached',) if staged else ()), "--", *config.directory_names()),
        ).stdout
        paths = tuple(sorted(path for path in paths_output.splitlines() if path.strip()))
        truncated = len(output) > self._max_output_chars
        return ObsidianGitDiff(project=project, text=output[: self._max_output_chars], paths=paths, truncated=truncated)

    def commit(self, *, project: ProjectRef, message: str, requested_by: str) -> ObsidianGitCommit:
        if not message.strip():
            raise ValueError("Git commit message is required")
        if not requested_by.strip():
            raise ValueError("Git commit requester is required")
        config = self._config(project)
        root = self._require_repo(config)
        unsafe = self._scan_vault(config)
        if unsafe:
            return ObsidianGitCommit(project=project, committed=False, error="; ".join(unsafe))
        status = self.status(project=project)
        if status.unsafe_paths:
            return ObsidianGitCommit(project=project, committed=False, error="unsafe Vault paths: " + ", ".join(status.unsafe_paths))
        self._run(root, ("add", "--", *config.directory_names()))
        staged = self._run(root, ("diff", "--cached", "--quiet"), check=False)
        if staged.returncode == 0:
            return ObsidianGitCommit(project=project, committed=False, message=message, changed_paths=status.changed_paths)
        changed = self._run(root, ("diff", "--cached", "--name-only")).stdout.splitlines()
        committed = self._run(root, ("commit", "-m", message))
        commit_id = self._run(root, ("rev-parse", "HEAD")).stdout.strip()
        return ObsidianGitCommit(
            project=project,
            committed=True,
            commit_id=commit_id,
            message=message,
            changed_paths=tuple(sorted(path for path in changed if path.strip())),
        )

    def rollback(self, *, project: ProjectRef, revision: str, paths: tuple[str, ...], force: bool = False) -> tuple[str, ...]:
        if not revision.strip() or revision.startswith("-") or any(char.isspace() for char in revision):
            raise ValueError("invalid Git revision")
        config = self._config(project)
        root = self._require_repo(config)
        normalized = tuple(assert_safe_path(config, path) for path in paths)
        if not normalized:
            raise ValueError("rollback requires at least one controlled Vault path")
        if not force:
            current = self.status(project=project)
            dirty = set(current.changed_paths) & set(normalized)
            if dirty:
                raise ObsidianError("rollback refused while selected Vault paths have local edits")
        self._run(root, ("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"))
        self._run(root, ("restore", "--source", revision, "--staged", "--worktree", "--", *normalized))
        return normalized

    def recovery_check(self, *, project: ProjectRef) -> ObsidianRecoveryReport:
        config = self._config(project)
        root = self._require_repo(config)
        manifest = ManifestStore(config)
        error = None
        try:
            current = manifest.load()
            if current is None:
                return ObsidianRecoveryReport(project=project, manifest_status="missing", recoverable_from_git=self._has_manifest_in_head(root))
            missing: list[str] = []
            changed: list[str] = []
            for entry in current.entries:
                path = resolve_vault_path(config, entry.relative_path)
                if not path.exists():
                    missing.append(entry.relative_path)
                elif _logical_file_hash(path) != entry.content_hash:
                    changed.append(entry.relative_path)
            return ObsidianRecoveryReport(
                project=project,
                manifest_status="valid",
                missing_paths=tuple(missing),
                changed_paths=tuple(changed),
            )
        except Exception as exc:  # noqa: BLE001 - report recovery state, do not hide it
            error = str(exc)
        return ObsidianRecoveryReport(
            project=project,
            manifest_status="corrupt",
            manifest_error=error,
            recoverable_from_git=self._has_manifest_in_head(root),
            changed_paths=self.status(project=project).changed_paths,
        )

    def restore_manifest_from_git(self, *, project: ProjectRef, revision: str = "HEAD") -> str:
        config = self._config(project)
        root = self._require_repo(config)
        self._run(root, ("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"))
        payload = self._run(root, ("show", f"{revision}:{ManifestStore.MANIFEST_PATH}")).stdout
        parsed = json.loads(payload)
        restored = VaultManifest.model_validate(parsed)
        if restored.tenant_id != project.tenant_id or restored.project_id != project.project_id:
            raise ObsidianError("Git manifest project scope does not match the requested project")
        target = resolve_vault_path(config, ManifestStore.MANIFEST_PATH)
        assert_safe_content(payload, max_file_bytes=config.max_file_bytes)
        temporary = target.with_name(f".{target.name}.recovery.tmp")
        temporary.write_text(json.dumps(restored.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, target)
        return ManifestStore.MANIFEST_PATH

    def _config(self, project: ProjectRef) -> VaultConfig:
        config: VaultConfig = self._config_for_project(project)
        if not config.enabled:
            raise ObsidianError("Obsidian Vault is disabled")
        if not config.git_enabled:
            raise ObsidianError("Obsidian Git workflow is disabled")
        return config

    def _require_repo(self, config: VaultConfig) -> Path:
        root = validate_vault_root(config)
        if not self._is_initialized(root):
            raise ObsidianError("Vault is not an initialized Git repository")
        return root

    @staticmethod
    def _is_initialized(root: Path) -> bool:
        return (root / ".git").exists() or (root / ".git").is_file()

    def _has_manifest_in_head(self, root: Path) -> bool:
        return self._run(root, ("cat-file", "-e", f"HEAD:{ManifestStore.MANIFEST_PATH}"), check=False).returncode == 0

    def _scan_vault(self, config: VaultConfig) -> list[str]:
        root = validate_vault_root(config)
        findings: list[str] = []
        if not root.exists():
            return findings
        for path in root.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            try:
                normalized = assert_safe_path(config, relative)
                if path.stat().st_size > config.max_file_bytes:
                    findings.append(f"{normalized}: file exceeds configured byte limit")
                    continue
                if path.suffix.casefold() in {".md", ".json"}:
                    assert_safe_content(path.read_text(encoding="utf-8"), max_file_bytes=config.max_file_bytes)
            except Exception as exc:  # noqa: BLE001 - return bounded audit finding
                findings.append(f"{relative}: {exc}")
        return findings

    @staticmethod
    def _run(root: Path, args: tuple[str, ...], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "git command failed").strip()
            raise ObsidianError(detail[:2_000])
        return completed


def _ahead_behind(branch_line: str) -> tuple[int, int]:
    if "[" not in branch_line or "]" not in branch_line:
        return 0, 0
    payload = branch_line.split("[", 1)[1].split("]", 1)[0]
    ahead = behind = 0
    for item in payload.split(","):
        item = item.strip()
        if item.startswith("ahead "):
            ahead = int(item[6:])
        elif item.startswith("behind "):
            behind = int(item[7:])
    return ahead, behind


def _logical_file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_text(encoding="utf-8").encode("utf-8"))
    return digest.hexdigest()


__all__ = [
    "ObsidianGitCommit",
    "ObsidianGitDiff",
    "ObsidianGitService",
    "ObsidianGitStatus",
    "ObsidianRecoveryReport",
]
