from datetime import datetime, timezone

import pytest

from project_lens.application.obsidian_inbox_service import (
    InMemoryKnowledgeProposalStore,
    ObsidianInboxService,
    SQLiteKnowledgeProposalStore,
)
from project_lens.context.models import AccessContext
from project_lens.context.source_records import SourceRecord, SourceType
from project_lens.context.source_store import InMemorySourceRecordStore
from project_lens.domain.knowledge_proposal import KnowledgeProposalStatus
from project_lens.domain.models import ProjectRef
from project_lens.obsidian.frontmatter import serialize_frontmatter
from project_lens.obsidian.inbox import ObsidianInboxScanner
from project_lens.obsidian.models import VaultConfig
from project_lens.persistence.sqlite import SQLiteDatabase


PROJECT = ProjectRef(tenant_id="demo", project_id="payment")
ACCESS = AccessContext(
    tenant_id="demo",
    user_id="u1",
    permissions=frozenset({"project:payment:read"}),
)


def _config(tmp_path) -> VaultConfig:
    return VaultConfig(
        root=tmp_path / "vault",
        allowed_root=tmp_path,
        tenant_id="demo",
        project_id="payment",
        enabled=True,
    )


def _write_proposal(config: VaultConfig, *, name: str, body: str, **metadata: object) -> None:
    path = config.root / "40-inbox" / "human-proposals" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_frontmatter(metadata) + "\n\n" + body, encoding="utf-8")


def _record(content: str = "coupon guard") -> SourceRecord:
    return SourceRecord(
        source_id="req-1",
        source_type=SourceType.FEISHU_DOCUMENT,
        project_id="payment",
        tenant_id="demo",
        title="Coupon scope",
        revision="1",
        observed_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        status="published",
        authority_scope=("requirement_scope",),
        access_scope="project:payment:read",
        content_hash="a" * 64,
        content=content,
    )


def _service(config: VaultConfig, *, store=None, sources=None) -> ObsidianInboxService:
    return ObsidianInboxService(
        scanner=ObsidianInboxScanner(config_for_project=lambda _: config),
        proposal_store=store or InMemoryKnowledgeProposalStore(),
        source_store=sources or InMemorySourceRecordStore(),
    )


def _metadata(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "proposal_id": "proposal-1",
        "tenant_id": "demo",
        "project_id": "payment",
        "kind": "wiki_update",
        "status": "proposed",
        "author": "forged-author",
        "created_at": "2026-09-12T00:00:00Z",
        "evidence_ids": ["demo:payment:req-1:1"],
        "approval_required": True,
        "title": "Coupon scope",
    }
    values.update(overrides)
    return values


def test_import_matches_evidence_and_never_trusts_document_author(tmp_path) -> None:
    config = _config(tmp_path)
    _write_proposal(config, name="coupon.md", body="# Coupon scope\n\ncoupon guard", **_metadata())
    sources = InMemorySourceRecordStore()
    sources.put(_record())

    result = _service(config, sources=sources).import_inbox(
        project=PROJECT, access=ACCESS, created_by="trusted-u1"
    )

    assert len(result.imported) == 1
    proposal = result.imported[0]
    assert proposal.status is KnowledgeProposalStatus.PROPOSED
    assert proposal.evidence_ids == ("demo:payment:req-1:1",)
    assert proposal.created_by == "trusted-u1"


def test_import_is_idempotent_by_source_path_and_hash(tmp_path) -> None:
    config = _config(tmp_path)
    _write_proposal(config, name="coupon.md", body="coupon guard", **_metadata())
    service = _service(config)

    first = service.import_inbox(project=PROJECT, access=ACCESS, created_by="u1")
    second = service.import_inbox(project=PROJECT, access=ACCESS, created_by="u1")

    assert len(first.imported) == 1
    assert len(second.imported) == 0
    assert len(second.duplicates) == 1
    assert second.duplicates[0].id == first.imported[0].id


@pytest.mark.parametrize(
    "body, expected",
    [("unrelated text", KnowledgeProposalStatus.NEEDS_EVIDENCE),
     ("coupon guard and payment declined", KnowledgeProposalStatus.CONFLICTED)],
)
def test_import_marks_missing_or_conflicting_evidence(tmp_path, body, expected) -> None:
    config = _config(tmp_path)
    _write_proposal(config, name="proposal.md", body=body, **_metadata(evidence_ids=[]))
    sources = InMemorySourceRecordStore()
    sources.put(_record("coupon guard"))
    sources.put(_record("payment declined" ).model_copy(update={"source_id": "req-2", "content_hash": "b" * 64}))

    result = _service(config, sources=sources).import_inbox(
        project=PROJECT, access=ACCESS, created_by="u1"
    )
    assert result.imported[0].status is expected


@pytest.mark.parametrize(
    "metadata",
    [
        {"tenant_id": "other"},
        {"status": "confirmed"},
        {"approval_required": False},
        {"confirmed": True},
        {"approved_by": "admin"},
        {"decided_by": "admin"},
    ],
)
def test_import_rejects_scope_or_forged_approval_fields(tmp_path, metadata) -> None:
    config = _config(tmp_path)
    _write_proposal(config, name="invalid.md", body="proposal", **_metadata(**metadata))

    result = _service(config).import_inbox(project=PROJECT, access=ACCESS, created_by="u1")

    assert len(result.imported) == 0
    assert len(result.rejected) == 1


def test_import_rejects_path_outside_inbox_allowlist(tmp_path) -> None:
    config = _config(tmp_path)
    with pytest.raises(PermissionError):
        ObsidianInboxScanner(config_for_project=lambda _: config).read_candidate(
            project=PROJECT, relative_path="20-wiki/evil.md", created_by="u1"
        )


def test_propose_wiki_update_is_proposal_only_and_durable(tmp_path) -> None:
    config = _config(tmp_path)
    sources = InMemorySourceRecordStore()
    sources.put(_record())
    store = SQLiteKnowledgeProposalStore(SQLiteDatabase(str(tmp_path / "proposals.db")))
    service = _service(config, store=store, sources=sources)

    proposal = service.propose_wiki_update(
        project=PROJECT,
        access=ACCESS,
        created_by="hermes-user",
        title="Coupon scope",
        content="coupon guard",
    )
    loaded = store.get_by_source(proposal.source_path, proposal.source_hash)

    assert loaded == proposal
    assert proposal.status is KnowledgeProposalStatus.PROPOSED
    assert proposal.decided_by is None
    assert proposal.decision_reason is None
    assert (config.root / "50-review" / "pending-approval" / f"proposal-{proposal.id}.md").exists()


def test_import_requires_matching_access_tenant(tmp_path) -> None:
    with pytest.raises(PermissionError):
        _service(_config(tmp_path)).import_inbox(
            project=PROJECT,
            access=AccessContext(tenant_id="other", user_id="u1"),
            created_by="u1",
        )


def test_project_agent_exposes_inbox_as_approval_bound_proposal_tools(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from project_lens.config import settings
    from project_lens.context.source_records import SourceRecord
    from project_lens.main import create_app
    from tests.conftest import project_agent_headers

    original = {
        "obsidian_enabled": settings.obsidian_enabled,
        "obsidian_inbox_enabled": settings.obsidian_inbox_enabled,
        "obsidian_vault_root": settings.obsidian_vault_root,
        "obsidian_allowed_root": settings.obsidian_allowed_root,
    }
    monkeypatch.setattr(settings, "obsidian_enabled", True)
    monkeypatch.setattr(settings, "obsidian_inbox_enabled", True)
    monkeypatch.setattr(settings, "obsidian_vault_root", str(tmp_path / "vaults"))
    monkeypatch.setattr(settings, "obsidian_allowed_root", str(tmp_path))
    try:
        app = create_app(":memory:")
        client = TestClient(app)
        app.state.source_record_store.put(_record().model_copy(update={"project_id": "projectlens", "access_scope": "project:projectlens:read"}))
        config = _config(tmp_path / "vaults" / "projectlens" / "projectlens")
        # _config's root is intentionally replaced with the production path.
        config = config.model_copy(
            update={
                "root": tmp_path / "vaults" / "projectlens" / "projectlens",
                "allowed_root": tmp_path,
            }
        )
        _write_proposal(config, name="coupon.md", body="coupon guard", **_metadata(project_id="projectlens", evidence_ids=[]))
        headers = project_agent_headers(actor_id="u1", chat_id="chat-1", tenant_key="demo")
        catalog = client.get("/api/v1/project-agent/tools", headers=headers).json()["tools"]
        by_name = {item["name"]: item for item in catalog}
        assert by_name["projectlens_import_obsidian_inbox"]["category"] == "propose"
        assert by_name["projectlens_import_obsidian_inbox"]["requires_approval"] is True
        assert by_name["projectlens_import_obsidian_inbox"]["allow_apply"] is False

        response = client.post(
            "/api/v1/project-agent/tools/call",
            json={
                "tool_name": "projectlens_import_obsidian_inbox",
                "project": {"tenant_id": "demo", "project_id": "projectlens"},
                "user_id": "u1",
                "chat_id": "chat-1",
                "arguments": {"path": "40-inbox/human-proposals/coupon.md"},
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["result"]["proposal_only"] is True
        assert payload["result"]["imported"][0]["status"] == "proposed"
        assert payload["audit_ref"]["approval_required"] is True
        assert payload["audit_ref"]["allow_apply"] is False
        assert app.state.memory_store.list_memories(ProjectRef(tenant_id="demo", project_id="projectlens")) == ()
    finally:
        for name, value in original.items():
            setattr(settings, name, value)
