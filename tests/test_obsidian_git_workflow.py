import hashlib
import subprocess
from datetime import datetime, timezone

import pytest

from project_lens.domain.models import ProjectRef
from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.git import ObsidianGitService
from project_lens.obsidian.manifest import ManifestStore, VaultManifest, VaultManifestEntry
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.paths import ensure_vault_layout


PROJECT = ProjectRef(tenant_id="demo", project_id="payment")


def _config(tmp_path) -> VaultConfig:
    return VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=True,
        git_enabled=True,
    )


def _git(root, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _service(config: VaultConfig) -> ObsidianGitService:
    return ObsidianGitService(config_for_project=lambda _: config)


def _commit_setup(config: VaultConfig) -> ObsidianGitService:
    ensure_vault_layout(config)
    (config.root / "00-home" / "Home.md").write_text("# Project\n", encoding="utf-8")
    service = _service(config)
    service.ensure_repository(project=PROJECT)
    _git(config.root, "config", "user.name", "ProjectLens Test")
    _git(config.root, "config", "user.email", "projectlens@example.test")
    created = service.commit(project=PROJECT, message="initial vault", requested_by="u1")
    assert created.committed is True
    return service


def test_git_status_diff_commit_and_rollback_stay_inside_vault(tmp_path) -> None:
    config = _config(tmp_path)
    service = _commit_setup(config)
    initial = _git(config.root, "rev-parse", "HEAD")
    (config.root / "00-home" / "Home.md").write_text("# Updated\n", encoding="utf-8")

    status = service.status(project=PROJECT)
    assert status.initialized is True
    assert status.clean is False
    assert status.changed_paths == ("00-home/Home.md",)
    diff = service.diff(project=PROJECT)
    assert "Updated" in diff.text
    assert diff.paths == ("00-home/Home.md",)

    committed = service.commit(project=PROJECT, message="update home", requested_by="hermes-user")
    assert committed.committed is True
    assert committed.commit_id
    assert service.status(project=PROJECT).clean is True

    restored = service.rollback(
        project=PROJECT,
        revision=initial,
        paths=("00-home/Home.md",),
    )
    assert restored == ("00-home/Home.md",)
    assert (config.root / "00-home" / "Home.md").read_text(encoding="utf-8") == "# Project\n"


def test_git_rollback_refuses_unreviewed_local_edits(tmp_path) -> None:
    config = _config(tmp_path)
    service = _commit_setup(config)
    revision = _git(config.root, "rev-parse", "HEAD")
    (config.root / "00-home" / "Home.md").write_text("# Human edit\n", encoding="utf-8")

    with pytest.raises(ObsidianError, match="local edits"):
        service.rollback(project=PROJECT, revision=revision, paths=("00-home/Home.md",))


def test_git_commit_rejects_forbidden_vault_files(tmp_path) -> None:
    config = _config(tmp_path)
    service = _commit_setup(config)
    (config.root / "90-system" / "leaked.db").write_text("private", encoding="utf-8")

    result = service.commit(project=PROJECT, message="unsafe", requested_by="u1")

    assert result.committed is False
    assert "forbidden Vault path component" in (result.error or "")


def test_manifest_recovery_uses_last_git_commit(tmp_path) -> None:
    config = _config(tmp_path)
    ensure_vault_layout(config)
    ManifestStore(config).save(VaultManifest(tenant_id="demo", project_id="payment"))
    service = _service(config)
    service.ensure_repository(project=PROJECT)
    _git(config.root, "config", "user.name", "ProjectLens Test")
    _git(config.root, "config", "user.email", "projectlens@example.test")
    assert service.commit(project=PROJECT, message="manifest", requested_by="u1").committed

    manifest_path = config.root / "90-system" / "manifest.json"
    manifest_path.write_text("{broken", encoding="utf-8")
    report = service.recovery_check(project=PROJECT)
    assert report.manifest_status == "corrupt"
    assert report.recoverable_from_git is True

    assert service.restore_manifest_from_git(project=PROJECT) == "90-system/manifest.json"
    assert ManifestStore(config).load() == VaultManifest(tenant_id="demo", project_id="payment")


def test_recovery_check_reports_manifest_tracked_file_changes(tmp_path) -> None:
    config = _config(tmp_path)
    service = _commit_setup(config)
    ManifestStore(config).save(
        VaultManifest(
            tenant_id="demo",
            project_id="payment",
            entries=(
                VaultManifestEntry(
                    relative_path="00-home/Home.md",
                    content_hash=hashlib.sha256("# Project\n".encode()).hexdigest(),
                    status="draft",
                    exported_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
                ),
            ),
        )
    )
    (config.root / "00-home" / "Home.md").write_text("# Human edit\n", encoding="utf-8")

    report = service.recovery_check(project=PROJECT)

    assert report.manifest_status == "valid"
    assert report.changed_paths == ("00-home/Home.md",)


def test_git_workflow_is_disabled_by_default(tmp_path) -> None:
    config = _config(tmp_path).model_copy(update={"git_enabled": False})
    service = ObsidianGitService(config_for_project=lambda _: config)

    status = service.status(project=PROJECT)
    assert status.enabled is False
    with pytest.raises(ObsidianError, match="disabled"):
        service.ensure_repository(project=PROJECT)
