"""ProjectLens tool envelope API for Hermes runtime integration."""

from __future__ import annotations

from fastapi.testclient import TestClient

from project_lens.main import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _project_payload(project_id: str = "payment") -> dict[str, str]:
    return {
        "tenant_id": "demo",
        "project_id": project_id,
    }


def _call_payload(
    *,
    tool_name: str,
    arguments: dict[str, object],
    project_id: str = "payment",
    user_id: str = "u1",
    chat_id: str = "chat-1",
) -> dict[str, object]:
    return {
        "tool_name": tool_name,
        "project": _project_payload(project_id),
        "user_id": user_id,
        "chat_id": chat_id,
        "arguments": arguments,
    }


def test_project_agent_tools_list_is_read_only_and_namespaced() -> None:
    response = _client().get("/api/v1/project-agent/tools")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    names = {item["name"] for item in body["tools"]}
    assert names == {
        "projectlens_search_context",
        "projectlens_read_project_file",
        "projectlens_query_graph",
        "projectlens_authorized_evidence",
        "projectlens_list_knowledge_gaps",
    }
    assert all(item["category"] == "read" for item in body["tools"])
    assert all(item["allow_apply"] is False for item in body["tools"])
    assert not any("deploy" in name or "apply" in name or "restart" in name for name in names)


def test_project_agent_tool_call_search_context_returns_envelope() -> None:
    response = _client().post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_search_context",
            arguments={"query": "create_order coupon", "limit": 3},
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["tool_name"] == "projectlens_search_context"
    assert body["internal_tool_name"] == "search_context"
    assert body["tool_result_id"]
    assert body["summary"]
    assert body["audit_ref"]["allow_apply"] is False
    assert body["audit_ref"]["project_id"] == "payment"
    assert body["audit_ref"]["tool_name"] == "projectlens_search_context"
    assert body["visibility_scope"]["project_id"] == "payment"
    assert isinstance(body["citations"], list)
    assert isinstance(body["evidence_refs"], list)
    assert "content" not in body


def test_project_agent_tool_call_unknown_and_write_tools_are_denied() -> None:
    client = _client()
    for tool_name in (
        "projectlens_apply_patch",
        "projectlens_create_pr",
        "projectlens_deploy",
        "shell",
        "unknown_tool",
    ):
        response = client.post(
            "/api/v1/project-agent/tools/call",
            json=_call_payload(tool_name=tool_name, arguments={}),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["error_code"] in {"TOOL_NOT_ALLOWED", "UNKNOWN_TOOL"}
        assert body["audit_ref"]["allow_apply"] is False
        assert "retry" in body["agent_recovery_hint"].lower() or "read-only" in body[
            "agent_recovery_hint"
        ].lower()


def test_project_agent_tool_call_read_file_respects_role_chat_scope() -> None:
    client = _client()
    allowed = client.post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_read_project_file",
            arguments={"path": "src/order_service.py"},
        ),
    )
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["ok"] is True
    assert body["citations"]
    assert all("content" not in cite for cite in body["citations"])

    denied = client.post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_read_project_file",
            arguments={"path": "secrets/prod.env"},
        ),
    )
    assert denied.status_code == 200
    error = denied.json()
    assert error["ok"] is False
    assert error["error_code"] in {"TOOL_EXECUTION_FAILED", "ACCESS_DENIED"}
    assert error["audit_ref"]["allow_apply"] is False


def test_project_agent_tool_call_can_switch_project_space() -> None:
    client = _client()
    response = client.post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_search_context",
            project_id="crm",
            arguments={"query": "contact", "limit": 3},
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["project"]["project_id"] == "crm"
    assert body["audit_ref"]["project_id"] == "crm"
    assert body["visibility_scope"]["project_id"] == "crm"


def test_project_agent_tool_call_query_graph_returns_envelope() -> None:
    response = _client().post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_query_graph",
            arguments={"limit": 5},
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["tool_name"] == "projectlens_query_graph"
    assert body["internal_tool_name"] == "query_graph"
    assert body["summary"]
    assert "path" in body["summary"].lower() or "query_graph" in body["summary"]
    assert body["audit_ref"]["allow_apply"] is False
    assert isinstance(body["citations"], list)
    assert isinstance(body["evidence_refs"], list)
    assert "agent_recovery_hint" not in body or body.get("ok") is True


def test_project_agent_tool_call_list_knowledge_gaps_returns_envelope() -> None:
    response = _client().post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_list_knowledge_gaps",
            arguments={},
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["tool_name"] == "projectlens_list_knowledge_gaps"
    assert body["internal_tool_name"] == "list_knowledge_gaps"
    assert body["summary"]
    assert "gap" in body["summary"].lower()
    assert body["audit_ref"]["allow_apply"] is False
    assert isinstance(body["citations"], list)
    assert isinstance(body["evidence_refs"], list)


def test_project_agent_tool_call_errors_include_agent_recovery_hint() -> None:
    response = _client().post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_grep_project",
            arguments={"query": "secret"},
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] in {"UNKNOWN_TOOL", "TOOL_NOT_ALLOWED"}
    assert body["agent_recovery_hint"]
    assert body["audit_ref"]["allow_apply"] is False


def test_projectlens_is_default_indexed_project_space() -> None:
    client = _client()
    response = client.post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_search_context",
            project_id="projectlens",
            arguments={"query": "Tool Envelope Hermes", "limit": 5},
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["project"]["project_id"] == "projectlens"
    assert body["audit_ref"]["allow_apply"] is False
    assert body["summary"]
    assert isinstance(body["citations"], list)

    # Demo spaces remain available.
    payment = client.post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_search_context",
            project_id="payment",
            arguments={"query": "create_order", "limit": 2},
        ),
    )
    crm = client.post(
        "/api/v1/project-agent/tools/call",
        json=_call_payload(
            tool_name="projectlens_search_context",
            project_id="crm",
            arguments={"query": "contact", "limit": 2},
        ),
    )
    assert payment.status_code == 200 and payment.json()["ok"] is True
    assert crm.status_code == 200 and crm.json()["ok"] is True
    assert payment.json()["project"]["project_id"] == "payment"
    assert crm.json()["project"]["project_id"] == "crm"


def test_hermes_plugin_defaults_to_projectlens() -> None:
    from project_lens.integrations.hermes_plugin.config import ProjectLensPluginConfig

    config = ProjectLensPluginConfig()
    assert config.default_project_id == "projectlens"
    assert config.project_for_chat(None)["project_id"] == "projectlens"
