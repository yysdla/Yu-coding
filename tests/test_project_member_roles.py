from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from project_lens.main import create_app
from project_lens.project_space.policies import ProjectRuntimeContextResolver
from project_lens.project_space.registry import ProjectRegistry, load_project_space_json
from tests.conftest import project_agent_headers

ROOT = Path(__file__).resolve().parents[1]


def test_same_actor_has_different_roles_in_payment_and_crm() -> None:
    payment = load_project_space_json(
        ROOT / "config" / "projects" / "payment.json",
        base_dir=ROOT,
    )
    crm = load_project_space_json(
        ROOT / "config" / "projects" / "crm.json",
        base_dir=ROOT,
    )
    resolver_payment = ProjectRuntimeContextResolver(
        project_registry=ProjectRegistry((payment,))
    )
    resolver_crm = ProjectRuntimeContextResolver(project_registry=ProjectRegistry((crm,)))

    payment_scope = resolver_payment.resolve(
        tenant_id="demo",
        project_id="payment",
        chat_id="chat-1",
        user_id="u-shared-1",
    )
    crm_scope = resolver_crm.resolve(
        tenant_id="demo",
        project_id="crm",
        chat_id="chat-1",
        user_id="u-shared-1",
    )

    assert payment_scope.effective_scope.role.value == "developer"
    assert crm_scope.effective_scope.role.value == "product"


def test_unknown_user_cannot_read_internal_code_via_tool_api() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/project-agent/tools/call",
        json={
            "tool_name": "projectlens_read_project_file",
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u_unknown",
            "chat_id": "chat-1",
            "arguments": {"path": "src/order_service.py"},
        },
        headers=project_agent_headers(actor_id="u_unknown"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] in {"ACCESS_DENIED", "TOOL_EXECUTION_FAILED", "TOOL_NOT_ALLOWED"}


def test_body_admin_spoof_does_not_elevate_permissions() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/project-agent/tools/call",
        json={
            "tool_name": "projectlens_read_project_file",
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "chat_id": "chat-1",
            "arguments": {"path": "src/order_service.py"},
        },
        headers=project_agent_headers(actor_id="u_unknown"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] in {"ACCESS_DENIED", "TOOL_EXECUTION_FAILED", "TOOL_NOT_ALLOWED"}
