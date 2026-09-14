from datetime import datetime, timezone

import pytest

from project_lens.application.wiki_compiler import InMemoryWikiDraftStore, WikiPageDraft, WikiPageType
from project_lens.context.models import AccessContext
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.context.source_store import InMemorySourceRecordStore
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.exporter import ObsidianExportService
from project_lens.obsidian.models import VaultConfig
from project_lens.obsidian.repository import ObsidianRepository


def test_wiki_repository_searches_and_reads_only_manifest_registered_derived_pages(tmp_path) -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    config = VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=True,
    )
    generated_at = datetime(2026, 9, 12, tzinfo=timezone.utc)
    sources = InMemorySourceRecordStore()
    sources.put(
        SourceRecord(
            source_id="req-1",
            source_type=SourceType.FEISHU_DOCUMENT,
            project_id="payment",
            tenant_id="demo",
            title="Coupon Scope",
            revision="1",
            observed_at=generated_at,
            status="published",
            authority_scope=("requirement_scope",),
            access_scope="project:payment:read",
            content_hash="a" * 64,
            content="coupon guard",
        )
    )
    drafts = InMemoryWikiDraftStore()
    drafts.put_many(
        (
            WikiPageDraft(
                tenant_id="demo",
                project_id="payment",
                page_type=WikiPageType.REQUIREMENTS,
                title="Coupon Requirements",
                status="review_required",
                content="# requirements\n\ncoupon guard",
                source_keys=("demo:payment:req-1:1",),
                generated_at=generated_at,
            ),
        )
    )
    ObsidianExportService(
        source_store=sources,
        wiki_store=drafts,
        config_for_project=lambda _: config,
    ).export_project(
        project=project,
        access=AccessContext(
            tenant_id="demo", user_id="u1", permissions=frozenset({"project:payment:read"})
        ),
        include_sources=True,
        include_review=False,
        now=generated_at,
    )
    repository = ObsidianRepository(config_for_project=lambda _: config, max_page_chars=1_000)
    found = repository.search(project=project, query="coupon", limit=8)
    assert any(item.page_type == "requirements" for item in found)
    page_path = next(item.path for item in found if item.page_type == "requirements")
    page = repository.read(project=project, relative_path=page_path)
    assert page.derived is True
    assert page.summary.source_keys == ("demo:payment:req-1:1",)
    assert "coupon guard" in page.content

    with pytest.raises(PermissionError):
        repository.read(project=project, relative_path="10-sources/feishu-docs/Coupon-Scope--1.md")


def test_wiki_repository_rejects_tampered_project_frontmatter(tmp_path) -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    config = VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=True,
    )
    from project_lens.obsidian.paths import ensure_vault_layout
    from project_lens.obsidian.frontmatter import serialize_frontmatter
    from project_lens.obsidian.manifest import ManifestStore, VaultManifest, VaultManifestEntry

    ensure_vault_layout(config)
    path = config.root / "20-wiki" / "requirements" / "tampered.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        serialize_frontmatter(
            {
                "tenant_id": "other",
                "project_id": "payment",
                "page_type": "requirements",
                "status": "confirmed",
                "derived": True,
                "source_keys": [],
            }
        )
        + "\n\n# Tampered\n",
        encoding="utf-8",
    )
    ManifestStore(config).save(
        VaultManifest(
            tenant_id="demo",
            project_id="payment",
            entries=(
                VaultManifestEntry(
                    relative_path="20-wiki/requirements/tampered.md",
                    content_hash="b" * 64,
                    status="confirmed",
                    exported_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
                ),
            ),
        )
    )
    with pytest.raises(PermissionError):
        ObsidianRepository(config_for_project=lambda _: config).read(
            project=project,
            relative_path="20-wiki/requirements/tampered.md",
        )


def test_hermes_wiki_tools_return_derived_manifest_bounded_content(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from project_lens.config import settings
    from project_lens.main import create_app
    from tests.conftest import project_agent_headers

    original = {
        "obsidian_enabled": settings.obsidian_enabled,
        "obsidian_vault_root": settings.obsidian_vault_root,
        "obsidian_allowed_root": settings.obsidian_allowed_root,
    }
    monkeypatch.setattr(settings, "obsidian_enabled", True)
    monkeypatch.setattr(settings, "obsidian_vault_root", str(tmp_path / "vaults"))
    monkeypatch.setattr(settings, "obsidian_allowed_root", str(tmp_path))
    try:
        app = create_app(":memory:")
        client = TestClient(app)
        project = {"tenant_id": "demo", "project_id": "projectlens"}
        permissions = ["project:projectlens:read"]
        drafted = client.post(
            "/api/v1/projects/wiki/draft",
            json={"project": project, "user_id": "u1", "permissions": permissions, "compile": True},
        )
        assert drafted.status_code == 200
        exported = client.post(
            "/api/v1/projects/obsidian/export",
            json={"project": project, "user_id": "u1", "permissions": permissions},
        )
        assert exported.status_code == 200, exported.text
        headers = project_agent_headers(actor_id="u1", chat_id="chat-1", tenant_key="demo")
        catalog = client.get("/api/v1/project-agent/tools", headers=headers)
        assert {item["name"] for item in catalog.json()["tools"]}.issuperset(
            {"projectlens_search_wiki", "projectlens_read_wiki_page"}
        )
        searched = client.post(
            "/api/v1/project-agent/tools/call",
            json={
                "tool_name": "projectlens_search_wiki",
                "project": project,
                "user_id": "u1",
                "chat_id": "chat-1",
                "arguments": {"query": "requirements"},
            },
            headers=headers,
        )
        assert searched.status_code == 200, searched.text
        search_payload = searched.json()
        assert search_payload["ok"] is True
        assert search_payload["result"]["derived"] is True
        page_path = search_payload["result"]["pages"][0]["path"]
        read = client.post(
            "/api/v1/project-agent/tools/call",
            json={
                "tool_name": "projectlens_read_wiki_page",
                "project": project,
                "user_id": "u1",
                "chat_id": "chat-1",
                "arguments": {"path": page_path, "max_chars": 1000},
            },
            headers=headers,
        )
        assert read.status_code == 200, read.text
        assert read.json()["result"]["derived"] is True
        assert "source_keys" in read.json()["result"]
        source_mirror = client.post(
            "/api/v1/project-agent/tools/call",
            json={
                "tool_name": "projectlens_read_wiki_page",
                "project": project,
                "user_id": "u1",
                "chat_id": "chat-1",
                "arguments": {"path": "10-sources/feishu-docs/not-registered.md"},
            },
            headers=headers,
        )
        assert source_mirror.json()["ok"] is False
        assert source_mirror.json()["error_code"] == "WIKI_PAGE_NOT_FOUND"
    finally:
        for name, value in original.items():
            setattr(settings, name, value)
