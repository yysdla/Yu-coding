import json
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient

from project_lens.application.feishu_doc_sync import FeishuDocumentSyncService
from project_lens.application.feishu_doc_sync_status import InMemoryFeishuDocSyncStatusStore
from project_lens.context.indexing.feishu_documents import FeishuDocumentIndexer
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.context.sources.feishu_docs import to_feishu_document_record
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.feishu_doc_sync import FeishuDocSyncStatusValue
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.docs_client import FeishuDocClient, FeishuDocRaw
from project_lens.integrations.feishu.http_adapter import FeishuTenantTokenProvider
from project_lens.main import create_app


class RecordingTransport:
    def __init__(self, routes: dict[str, dict[str, Any]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(f"{method} {url}")
        if "tenant_access_token" in url:
            return 200, {"code": 0, "tenant_access_token": "tok", "expire": 3600}
        for key, payload in self.routes.items():
            if key in url:
                return 200, payload
        return 404, {"code": 1770002, "msg": "not found"}


def _project() -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id="payment", service="order-service")


def test_to_feishu_document_record_maps_api_raw() -> None:
    raw = FeishuDocRaw(
        doc_token="docx_payment_owners",
        title="Owners",
        content="order-service owner is Ada",
        revision="3",
        updated_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        owner_user_id="ou_ada",
        doc_url="https://feishu.cn/docx/docx_payment_owners",
    )
    record = to_feishu_document_record(
        raw,
        project=_project(),
        access_scope="project:payment:read",
    )
    assert record.doc_token == "docx_payment_owners"
    assert record.revision == "3"
    assert record.owner_user_id == "ou_ada"
    assert "Ada" in record.content


def test_feishu_doc_client_fetches_meta_and_raw_content() -> None:
    transport = RecordingTransport(
        {
            "/documents/docx_payment_owners/raw_content": {
                "code": 0,
                "data": {"content": "order-service owner is Ada"},
            },
            "/documents/docx_payment_owners": {
                "code": 0,
                "data": {
                    "document": {
                        "document_id": "docx_payment_owners",
                        "title": "Owners",
                        "revision_id": 3,
                        "owner_id": "ou_ada",
                        "edit_time": 1721552400,
                    }
                },
            },
        }
    )
    client = FeishuDocClient(
        token_provider=FeishuTenantTokenProvider(
            app_id="app",
            app_secret="secret",
            transport=transport,
        ),
        transport=transport,
    )
    raw = client.get_document("docx_payment_owners")
    assert raw.title == "Owners"
    assert raw.revision == "3"
    assert raw.owner_user_id == "ou_ada"
    assert "Ada" in raw.content
    assert any("raw_content" in call for call in transport.calls)


def test_sync_indexes_new_revision_and_skips_unchanged() -> None:
    transport = RecordingTransport(
        {
            "/documents/docx_sync_1/raw_content": {
                "code": 0,
                "data": {"content": "order-service owner is Ada revision one"},
            },
            "/documents/docx_sync_1": {
                "code": 0,
                "data": {
                    "document": {
                        "document_id": "docx_sync_1",
                        "title": "Owners",
                        "revision_id": 1,
                        "owner_id": "ou_ada",
                    }
                },
            },
        }
    )
    index = InMemoryEvidenceIndex()
    status_store = InMemoryFeishuDocSyncStatusStore()
    service = FeishuDocumentSyncService(
        client=FeishuDocClient(
            token_provider=FeishuTenantTokenProvider(
                app_id="app",
                app_secret="secret",
                transport=transport,
            ),
            transport=transport,
        ),
        index=index,
        status_store=status_store,
    )
    first = service.sync_tokens(
        project=_project(),
        access_scope="project:payment:read",
        doc_tokens=("docx_sync_1",),
    )
    assert first.indexed >= 1
    assert first.skipped_unchanged == 0
    assert first.statuses[0].status == FeishuDocSyncStatusValue.SUCCESS
    assert first.statuses[0].revision == "1"
    assert first.statuses[0].access_scope == "project:payment:read"
    assert first.statuses[0].doc_url == "https://feishu.cn/docx/docx_sync_1"
    assert first.statuses[0].last_success_revision == "1"
    first_hash = next(
        item.content_hash
        for item in index.all()
        if item.metadata.get("doc_token") == "docx_sync_1"
    )

    second = service.sync_tokens(
        project=_project(),
        access_scope="project:payment:read",
        doc_tokens=("docx_sync_1",),
    )
    assert second.skipped_unchanged == 1
    assert second.indexed == 0
    assert second.statuses[0].status == FeishuDocSyncStatusValue.SKIPPED
    assert second.statuses[0].access_scope == "project:payment:read"
    assert second.statuses[0].doc_url == "https://feishu.cn/docx/docx_sync_1"
    assert second.statuses[0].last_success_revision == "1"

    transport.routes["/documents/docx_sync_1"] = {
        "code": 0,
        "data": {
            "document": {
                "document_id": "docx_sync_1",
                "title": "Owners",
                "revision_id": 2,
                "owner_id": "ou_ada",
            }
        },
    }
    transport.routes["/documents/docx_sync_1/raw_content"] = {
        "code": 0,
        "data": {"content": "order-service owner is Ada revision two"},
    }
    third = service.sync_tokens(
        project=_project(),
        access_scope="project:payment:read",
        doc_tokens=("docx_sync_1",),
    )
    assert third.indexed >= 1
    second_hash = next(
        item.content_hash
        for item in index.all()
        if item.metadata.get("doc_token") == "docx_sync_1"
    )
    assert first_hash != second_hash
    assert all(
        item.metadata.get("revision") == "2"
        for item in index.all()
        if item.metadata.get("doc_token") == "docx_sync_1"
    )
    recorded = status_store.get(_project(), "docx_sync_1")
    assert recorded is not None
    assert recorded.status == FeishuDocSyncStatusValue.SUCCESS
    assert recorded.revision == "2"
    assert recorded.last_success_revision == "2"
    assert recorded.doc_url == "https://feishu.cn/docx/docx_sync_1"


def test_sync_continues_when_one_token_fails() -> None:
    transport = RecordingTransport(
        {
            "/documents/docx_ok/raw_content": {
                "code": 0,
                "data": {"content": "payment architecture note"},
            },
            "/documents/docx_ok": {
                "code": 0,
                "data": {"document": {"document_id": "docx_ok", "title": "OK", "revision_id": 1}},
            },
        }
    )
    index = InMemoryEvidenceIndex()
    status_store = InMemoryFeishuDocSyncStatusStore()
    service = FeishuDocumentSyncService(
        client=FeishuDocClient(
            token_provider=FeishuTenantTokenProvider(
                app_id="app",
                app_secret="secret",
                transport=transport,
            ),
            transport=transport,
        ),
        index=index,
        status_store=status_store,
    )
    result = service.sync_tokens(
        project=_project(),
        access_scope="project:payment:read",
        doc_tokens=("docx_missing", "docx_ok"),
    )
    assert result.fetched == 1
    assert result.indexed >= 1
    assert any("docx_missing" in item for item in result.failed)
    failed = status_store.get(_project(), "docx_missing")
    ok = status_store.get(_project(), "docx_ok")
    assert failed is not None and failed.status == FeishuDocSyncStatusValue.FAILED
    assert failed.error
    assert failed.access_scope == "project:payment:read"
    assert ok is not None and ok.status == FeishuDocSyncStatusValue.SUCCESS
    assert ok.doc_url == "https://feishu.cn/docx/docx_ok"
    assert ok.access_scope == "project:payment:read"


def test_failed_sync_preserves_url_scope_and_last_success_revision() -> None:
    transport = RecordingTransport(
        {
            "/documents/docx_keep/raw_content": {
                "code": 0,
                "data": {"content": "order-service owner is Ada"},
            },
            "/documents/docx_keep": {
                "code": 0,
                "data": {
                    "document": {
                        "document_id": "docx_keep",
                        "title": "Keep",
                        "revision_id": 4,
                        "owner_id": "ou_ada",
                    }
                },
            },
        }
    )
    index = InMemoryEvidenceIndex()
    status_store = InMemoryFeishuDocSyncStatusStore()
    service = FeishuDocumentSyncService(
        client=FeishuDocClient(
            token_provider=FeishuTenantTokenProvider(
                app_id="app",
                app_secret="secret",
                transport=transport,
            ),
            transport=transport,
        ),
        index=index,
        status_store=status_store,
    )
    first = service.sync_tokens(
        project=_project(),
        access_scope="project:payment:read",
        doc_tokens=("docx_keep",),
    )
    assert first.statuses[0].status == FeishuDocSyncStatusValue.SUCCESS
    assert first.statuses[0].last_success_revision == "4"

    # Break fetch so the second sync fails and must preserve prior metadata.
    transport.routes.pop("/documents/docx_keep", None)
    transport.routes.pop("/documents/docx_keep/raw_content", None)
    second = service.sync_tokens(
        project=_project(),
        access_scope="project:payment:read",
        doc_tokens=("docx_keep",),
    )
    failed = second.statuses[0]
    assert failed.status == FeishuDocSyncStatusValue.FAILED
    assert failed.doc_url == "https://feishu.cn/docx/docx_keep"
    assert failed.access_scope == "project:payment:read"
    assert failed.revision == "4"
    assert failed.last_success_revision == "4"
    assert failed.owner_user_id == "ou_ada"


def test_synced_doc_respects_acl_for_retrieval() -> None:
    index = InMemoryEvidenceIndex()
    indexer = FeishuDocumentIndexer()
    record = to_feishu_document_record(
        FeishuDocRaw(
            doc_token="docx_acl",
            title="Secret",
            content="order-service owner is Ada",
            revision="1",
            updated_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            owner_user_id="ou_ada",
        ),
        project=_project(),
        access_scope="project:payment:secret",
    )
    index.add_many(indexer.index([record], project=_project()))
    from project_lens.context.engine import ContextEngine

    engine = ContextEngine(index)
    denied = engine.search(
        ContextQuery(text="owner Ada", project=_project(), limit=5),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:read"}),
        ),
    )
    allowed = engine.search(
        ContextQuery(text="owner Ada", project=_project(), limit=5),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:secret"}),
        ),
    )
    assert denied.evidence == ()
    assert allowed.evidence


def test_sync_api_requires_credentials_and_permission() -> None:
    app = create_app()
    # Force unavailable regardless of local .env credentials.
    app.state.feishu_doc_sync_service = FeishuDocumentSyncService(
        client=None,
        index=app.state.evidence_index,
    )
    client = TestClient(app)
    unavailable = client.post(
        "/api/v1/projects/feishu-docs/sync",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "permissions": ["project:payment:read"],
            "doc_tokens": ["docx_x"],
        },
    )
    assert unavailable.status_code == 503

    transport = RecordingTransport(
        {
            "/documents/docx_api/raw_content": {
                "code": 0,
                "data": {"content": "order-service owner is Ada from api"},
            },
            "/documents/docx_api": {
                "code": 0,
                "data": {
                    "document": {
                        "document_id": "docx_api",
                        "title": "API Owners",
                        "revision_id": 9,
                        "owner_id": "ou_ada",
                    }
                },
            },
        }
    )
    token_provider = FeishuTenantTokenProvider(
        app_id="app",
        app_secret="secret",
        transport=transport,
    )
    app.state.feishu_doc_sync_service = FeishuDocumentSyncService(
        client=FeishuDocClient(token_provider=token_provider, transport=transport),
        index=app.state.evidence_index,
    )

    forbidden = client.post(
        "/api/v1/projects/feishu-docs/sync",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "permissions": [],
            "doc_tokens": ["docx_api"],
        },
    )
    assert forbidden.status_code == 403

    ok = client.post(
        "/api/v1/projects/feishu-docs/sync",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "permissions": ["project:payment:read"],
            "doc_tokens": ["docx_api"],
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["fetched"] == 1
    assert body["indexed"] >= 1
    assert any(
        item.metadata.get("doc_token") == "docx_api" for item in app.state.evidence_index.all()
    )


def test_feishu_message_path_unaffected_when_doc_sync_unavailable() -> None:
    app = create_app()
    app.state.feishu_doc_sync_service = FeishuDocumentSyncService(
        client=None,
        index=app.state.evidence_index,
    )
    verifier = app.state.feishu_event_service._verifier
    verifier._verification_token = "project-lens-local-token"
    verifier._signing_secret = None
    from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger
    from project_lens.integrations.feishu.identity import parse_project_bindings

    app.state.feishu_event_service._identity_mapper = parse_project_bindings(
        "",
        default_project=ProjectRef(
            tenant_id="demo",
            project_id="payment",
            service="order-service",
            environment="production",
        ),
    )
    app.state.feishu_messenger = RecordingFeishuMessenger()
    app.state.feishu_event_service._messenger = app.state.feishu_messenger
    assert app.state.feishu_doc_sync_service.available is False
    client = TestClient(app)
    response = client.post(
        "/api/v1/feishu/events",
        json={
            "schema": "2.0",
            "token": "project-lens-local-token",
            "header": {
                "event_id": "doc-sync-unaffected",
                "event_type": "im.message.receive_v1",
                "tenant_key": "demo",
            },
            "event": {
                "sender": {"sender_id": {"user_id": "feishu-user-1"}},
                "message": {
                    "message_id": "m-doc-sync",
                    "chat_id": "chat-1",
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps({"text": "负责人是谁"}),
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
