from datetime import datetime, timezone

import pytest

from project_lens.application.wiki_compiler import InMemoryWikiDraftStore, WikiPageDraft, WikiPageType
from project_lens.context.models import AccessContext
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.context.source_store import InMemorySourceRecordStore
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.errors import ObsidianError
from project_lens.obsidian.exporter import ObsidianExportService
from project_lens.obsidian.frontmatter import parse_frontmatter
from project_lens.obsidian.manifest import ManifestStore
from project_lens.obsidian.models import VaultConfig


def _record(source_id: str, *, scope: str, content: str, revision: str = "1") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        source_type=SourceType.FEISHU_DOCUMENT,
        project_id="payment",
        tenant_id="demo",
        title="Project Scope / unsafe: name",
        raw_uri=f"https://example.test/{source_id}",
        revision=revision,
        observed_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        status="published",
        authority_scope=("requirement_scope",),
        access_scope=scope,
        content_hash=(source_id + revision).encode().hex()[:64].ljust(16, "0"),
        content=content,
    )


def test_export_is_acl_filtered_revisioned_and_idempotent(tmp_path) -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    config = VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=True,
    )
    sources = InMemorySourceRecordStore()
    sources.put(_record("allowed", scope="project:payment:read", content="approved scope"))
    sources.put(_record("denied", scope="project:payment:private", content="private scope"))
    drafts = InMemoryWikiDraftStore()
    drafts.put_many(
        (
            WikiPageDraft(
                tenant_id="demo",
                project_id="payment",
                page_type=WikiPageType.REQUIREMENTS,
                title="Payment Requirements",
                status="review_required",
                content="# requirements\n\napproved scope",
                source_keys=("demo:payment:allowed:1",),
                generated_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            ),
        )
    )
    service = ObsidianExportService(
        source_store=sources,
        wiki_store=drafts,
        config_for_project=lambda _: config,
    )
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({"project:payment:read"}),
    )
    now = datetime(2026, 9, 12, 1, tzinfo=timezone.utc)
    first = service.export_project(project=project, access=access, now=now)
    assert first.source_count == 1
    assert first.wiki_count >= 3
    assert first.exported_paths
    assert all("denied" not in path for path in first.exported_paths)
    assert not list((tmp_path / "vault").rglob("*private*"))
    assert (tmp_path / "vault" / "00-home" / "Home.md").exists()
    assert (tmp_path / "vault" / "50-review" / "Knowledge-Gaps.md").exists()

    source_path = next(
        path for path in (tmp_path / "vault" / "10-sources" / "feishu-docs").glob("*.md")
    )
    metadata, body = parse_frontmatter(source_path.read_text(encoding="utf-8"))
    assert metadata["source_key"] == "demo:payment:allowed:1"
    assert metadata["immutable"] is True
    assert body == "approved scope"

    second = service.export_project(project=project, access=access, now=now)
    assert second.exported_paths == ()
    assert set(second.skipped_paths) == set(first.exported_paths)


def test_export_preserves_manual_edit_and_records_conflict(tmp_path) -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    config = VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=True,
    )
    sources = InMemorySourceRecordStore()
    sources.put(_record("allowed", scope="project:payment:read", content="approved scope"))
    service = ObsidianExportService(
        source_store=sources,
        wiki_store=InMemoryWikiDraftStore(),
        config_for_project=lambda _: config,
    )
    first = service.export_project(
        project=project,
        access=AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:read"}),
        ),
        now=datetime(2026, 9, 12, tzinfo=timezone.utc),
    )
    source_path = next(path for path in first.exported_paths if path.startswith("10-sources/"))
    target = config.root / source_path
    target.write_text(target.read_text(encoding="utf-8") + "\nmanual note", encoding="utf-8")

    second = service.export_project(
        project=project,
        access=AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:read"}),
        ),
        now=datetime(2026, 9, 12, tzinfo=timezone.utc),
    )

    assert second.exported_paths == ()
    assert source_path in second.conflicted_paths
    assert "manual note" in target.read_text(encoding="utf-8")
    assert ManifestStore(config).load().last_export_id == first.export_id


def test_export_restores_partial_writes_when_a_later_write_fails(tmp_path, monkeypatch) -> None:
    from project_lens.obsidian import exporter as exporter_module

    project = ProjectRef(tenant_id="demo", project_id="payment")
    config = VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=True,
    )
    sources = InMemorySourceRecordStore()
    sources.put(_record("allowed", scope="project:payment:read", content="approved scope"))
    service = ObsidianExportService(
        source_store=sources,
        wiki_store=InMemoryWikiDraftStore(),
        config_for_project=lambda _: config,
    )
    original_write = exporter_module._atomic_write
    calls = 0

    def fail_on_second(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        original_write(path, content)

    monkeypatch.setattr(exporter_module, "_atomic_write", fail_on_second)
    with pytest.raises(OSError, match="simulated disk failure"):
        service.export_project(
            project=project,
            access=AccessContext(
                tenant_id="demo",
                user_id="u1",
                permissions=frozenset({"project:payment:read"}),
            ),
            now=datetime(2026, 9, 12, tzinfo=timezone.utc),
        )

    assert not list((config.root / "10-sources").rglob("*.md"))
    assert not (config.root / "90-system" / "manifest.json").exists()


def test_export_requires_matching_tenant_and_enabled_config(tmp_path) -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    sources = InMemorySourceRecordStore()
    drafts = InMemoryWikiDraftStore()
    disabled = VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=False,
    )
    service = ObsidianExportService(
        source_store=sources,
        wiki_store=drafts,
        config_for_project=lambda _: disabled,
    )
    with pytest.raises(ObsidianError, match="disabled"):
        service.export_project(
            project=project,
            access=AccessContext(
                tenant_id="demo", user_id="u1", permissions=frozenset()
            ),
        )
    with pytest.raises(ObsidianError, match="tenant"):
        service.export_project(
            project=project,
            access=AccessContext(
                tenant_id="other", user_id="u1", permissions=frozenset()
            ),
        )


def test_obsidian_export_api_is_disabled_by_default() -> None:
    from fastapi.testclient import TestClient
    from project_lens.main import create_app

    response = TestClient(create_app(":memory:")).post(
        "/api/v1/projects/obsidian/export",
        json={
            "project": {"tenant_id": "demo", "project_id": "projectlens"},
            "user_id": "u1",
            "permissions": ["project:projectlens:read"],
        },
    )
    assert response.status_code == 503


def test_obsidian_export_api_enforces_project_scope_and_writes_vault(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from project_lens.config import settings
    from project_lens.main import create_app

    original = {
        "obsidian_enabled": settings.obsidian_enabled,
        "obsidian_vault_root": settings.obsidian_vault_root,
        "obsidian_allowed_root": settings.obsidian_allowed_root,
    }
    monkeypatch.setattr(settings, "obsidian_enabled", True)
    monkeypatch.setattr(settings, "obsidian_vault_root", str(tmp_path / "vault-parent"))
    monkeypatch.setattr(settings, "obsidian_allowed_root", str(tmp_path))
    try:
        app = create_app(":memory:")
        client = TestClient(app)
        denied = client.post(
            "/api/v1/projects/obsidian/export",
            json={
                "project": {"tenant_id": "demo", "project_id": "projectlens"},
                "user_id": "u1",
                "permissions": [],
            },
        )
        assert denied.status_code == 403
        exported = client.post(
            "/api/v1/projects/obsidian/export",
            json={
                "project": {"tenant_id": "demo", "project_id": "projectlens"},
                "user_id": "u1",
                "permissions": ["project:projectlens:read"],
                "include_sources": False,
            },
        )
        assert exported.status_code == 200, exported.text
        assert exported.json()["project"]["project_id"] == "projectlens"
        assert (tmp_path / "vault-parent" / "projectlens" / "projectlens" / "00-home" / "Home.md").exists()
    finally:
        for name, value in original.items():
            setattr(settings, name, value)
