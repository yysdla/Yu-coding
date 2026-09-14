from datetime import datetime, timezone

import pytest

from project_lens.obsidian import (
    ManifestStore,
    SafetyViolationError,
    VaultConfig,
    VaultManifest,
    VaultManifestEntry,
    assert_safe_content,
    assert_safe_path,
    ensure_vault_layout,
    parse_frontmatter,
    serialize_frontmatter,
)
from project_lens.obsidian.errors import ObsidianError, VaultConfigurationError


def _config(tmp_path) -> VaultConfig:
    return VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="projectlens",
        enabled=True,
    )


def test_vault_layout_is_fixed_and_root_is_confined(tmp_path) -> None:
    config = _config(tmp_path)
    root = ensure_vault_layout(config)
    assert root == (tmp_path / "vault").resolve()
    assert (root / "10-sources").is_dir()
    assert (root / "90-system").is_dir()

    with pytest.raises(VaultConfigurationError):
        ensure_vault_layout(config.model_copy(update={"root": tmp_path.parent}))


@pytest.mark.parametrize(
    "relative_path",
    ["../outside.md", "10-sources/../../outside.md", "C:/outside.md", "\\\\server\\share\\x.md", "docs/x.md"],
)
def test_paths_cannot_escape_or_leave_controlled_directories(tmp_path, relative_path: str) -> None:
    with pytest.raises(SafetyViolationError):
        assert_safe_path(_config(tmp_path), relative_path)


@pytest.mark.parametrize(
    "relative_path",
    ["90-system/.env", "90-system/project_lens.db", "10-sources/secrets.json"],
)
def test_sensitive_files_are_rejected(tmp_path, relative_path: str) -> None:
    with pytest.raises(SafetyViolationError):
        assert_safe_path(_config(tmp_path), relative_path)


def test_content_secret_and_size_scanning_is_fail_closed() -> None:
    with pytest.raises(SafetyViolationError):
        assert_safe_content("api_key: sk-example-secret", max_file_bytes=1000)
    with pytest.raises(SafetyViolationError):
        assert_safe_content("x" * 11, max_file_bytes=10)


def test_frontmatter_round_trip_is_deterministic() -> None:
    metadata = {
        "tenant_id": "demo",
        "project_id": "projectlens",
        "derived": True,
        "source_keys": ["demo:projectlens:doc:1", "demo:projectlens:doc:2"],
        "generated_at": "2026-09-12T00:00:00+00:00",
    }
    encoded = serialize_frontmatter(metadata)
    decoded, body = parse_frontmatter(encoded + "\n\n# Title\n")
    assert decoded == metadata
    assert body == "# Title"
    assert serialize_frontmatter(metadata) == encoded


def test_manifest_round_trip_is_scoped_and_atomic(tmp_path) -> None:
    config = _config(tmp_path)
    store = ManifestStore(config)
    timestamp = datetime(2026, 9, 12, tzinfo=timezone.utc)
    manifest = VaultManifest(
        tenant_id="demo",
        project_id="projectlens",
        last_export_id="export-1",
        last_success_at=timestamp,
        entries=(
            VaultManifestEntry(
                relative_path="20-wiki/requirements/scope.md",
                content_hash="a" * 64,
                source_keys=("demo:projectlens:doc:1",),
                status="review_required",
                exported_at=timestamp,
            ),
        ),
    )
    store.save(manifest)
    assert store.load() == manifest
    assert store.path.read_text(encoding="utf-8").endswith("\n")
    assert not store.path.with_name(".manifest.json.tmp").exists()

    with pytest.raises(ObsidianError):
        store.save(manifest.model_copy(update={"tenant_id": "other"}))
