from __future__ import annotations

from fastapi.testclient import TestClient

from project_lens.main import create_app
from tests.conftest import project_agent_headers


def _client() -> TestClient:
    return TestClient(create_app())


def test_project_spaces_list_exposes_manifest_summary_without_file_contents() -> None:
    response = _client().get("/api/v1/project-agent/project-spaces")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    ids = {(item["tenant_id"], item["project_id"]) for item in body["project_spaces"]}
    assert ("demo", "projectlens") in ids
    assert ("demo", "payment") in ids
    projectlens = next(
        item for item in body["project_spaces"] if item["project_id"] == "projectlens"
    )
    assert projectlens["rag_namespace"] == "demo:projectlens:rag"
    assert projectlens["allow_apply"] is False
    assert "content" not in projectlens


def test_project_space_detail_exposes_connectors_and_validation_report() -> None:
    response = _client().get("/api/v1/project-agent/project-spaces/demo/payment")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["project_space"]["project_id"] == "payment"
    assert body["project_space"]["source_connectors"]
    assert body["project_space"]["feishu_chat_bindings"]
    assert body["validation"]["ok"] is True
    assert body["allow_apply"] is False


def test_project_space_scope_inspect_resolves_role_before_generation() -> None:
    response = _client().get(
        "/api/v1/project-agent/project-spaces/demo/projectlens/scope",
        params={"user_id": "u1", "chat_id": "chat-1"},
        headers=project_agent_headers(actor_id="u1", chat_id="chat-1"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["effective_scope"]["role"] == "developer"
    assert "read_project_file" in body["effective_scope"]["allowed_tools"]
    assert body["allow_apply"] is False


def test_project_space_scope_inspect_reports_unknown_project() -> None:
    response = _client().get(
        "/api/v1/project-agent/project-spaces/demo/missing/scope",
        params={"user_id": "u1", "chat_id": "chat-1"},
        headers=project_agent_headers(actor_id="u1", chat_id="chat-1"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "unknown project space: demo/missing"
