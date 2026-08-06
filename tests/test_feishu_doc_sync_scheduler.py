import time
from pathlib import Path

from fastapi.testclient import TestClient

from project_lens.application.feishu_doc_sync import FeishuDocumentSyncService
from project_lens.application.feishu_doc_sync_runner import sync_registered_projects
from project_lens.application.feishu_doc_sync_scheduler import FeishuDocSyncScheduler
from project_lens.application.feishu_doc_sync_status import InMemoryFeishuDocSyncStatusStore
from project_lens.context.bootstrap import (
    LocalContextSources,
    LocalProjectRegistration,
)
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import ProjectRef
from project_lens.integrations.feishu.docs_client import FeishuDocClient
from project_lens.integrations.feishu.http_adapter import FeishuTenantTokenProvider
from project_lens.main import create_app
from tests.test_feishu_doc_sync import RecordingTransport

DEMO = Path(__file__).parents[1] / "examples" / "payment_service"


def _registration(tokens: tuple[str, ...] = ("docx_sched_1",)) -> LocalProjectRegistration:
    return LocalProjectRegistration(
        project=ProjectRef(tenant_id="demo", project_id="payment", service="order-service"),
        sources=LocalContextSources(
            repository_root=DEMO / "src",
            feishu_doc_tokens=tokens,
        ),
        access_scope="project:payment:read",
    )


def _service(transport: RecordingTransport) -> FeishuDocumentSyncService:
    token_provider = FeishuTenantTokenProvider(
        app_id="app",
        app_secret="secret",
        transport=transport,
    )
    return FeishuDocumentSyncService(
        client=FeishuDocClient(token_provider=token_provider, transport=transport),
        index=InMemoryEvidenceIndex(),
        status_store=InMemoryFeishuDocSyncStatusStore(),
    )


def test_sync_registered_projects_isolates_project_failures() -> None:
    transport = RecordingTransport(
        {
            "/documents/docx_ok/raw_content": {
                "code": 0,
                "data": {"content": "synced content for scheduler"},
            },
            "/documents/docx_ok": {
                "code": 0,
                "data": {
                    "document": {
                        "document_id": "docx_ok",
                        "title": "OK",
                        "revision_id": 1,
                    }
                },
            },
        }
    )
    service = _service(transport)
    registrations = (
        LocalProjectRegistration(
            project=ProjectRef(tenant_id="demo", project_id="bad"),
            sources=LocalContextSources(
                repository_root=DEMO / "src",
                feishu_doc_tokens=("docx_missing",),
            ),
            access_scope="project:payment:read",
        ),
        LocalProjectRegistration(
            project=ProjectRef(tenant_id="demo", project_id="good"),
            sources=LocalContextSources(
                repository_root=DEMO / "src",
                feishu_doc_tokens=("docx_ok",),
            ),
            access_scope="project:payment:read",
        ),
    )
    summary = sync_registered_projects(service, registrations)
    assert summary.project_count == 2
    assert summary.indexed >= 1
    assert summary.failed >= 1
    assert any(item.project_id == "good" for item in summary.results)


def test_scheduler_run_once_and_interval_do_not_raise() -> None:
    transport = RecordingTransport(
        {
            "/documents/docx_sched_1/raw_content": {
                "code": 0,
                "data": {"content": "interval sync content"},
            },
            "/documents/docx_sched_1": {
                "code": 0,
                "data": {
                    "document": {
                        "document_id": "docx_sched_1",
                        "title": "Sched",
                        "revision_id": 2,
                    }
                },
            },
        }
    )
    service = _service(transport)
    scheduler = FeishuDocSyncScheduler(
        service=service,
        registrations=(_registration(),),
        on_startup=True,
        interval_seconds=1,
    )
    summary = scheduler.run_once()
    assert summary.indexed >= 1
    assert scheduler.last_summary is not None

    scheduler.start()
    assert scheduler.running is True
    time.sleep(1.2)
    scheduler.stop(timeout=2.0)
    assert scheduler.last_summary is not None
    assert scheduler.last_summary.requested >= 1


def test_create_app_scheduler_defaults_off_and_chat_still_works() -> None:
    app = create_app()
    assert isinstance(app.state.feishu_doc_sync_scheduler, FeishuDocSyncScheduler)
    with TestClient(app) as client:
        # Defaults keep auto-sync off so tests/chat are not blocked by remote APIs.
        assert app.state.feishu_doc_sync_scheduler.running is False
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_unavailable_client_sync_runner_returns_error_summary() -> None:
    service = FeishuDocumentSyncService(
        client=None,
        index=InMemoryEvidenceIndex(),
        status_store=InMemoryFeishuDocSyncStatusStore(),
    )
    summary = sync_registered_projects(service, (_registration(),))
    assert summary.project_count == 0
    assert summary.errors
