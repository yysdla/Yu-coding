"""ProjectAgent structured fact lookup for the first cross-department pilot."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from project_lens.context.source_records import FactType, SourceRecord, SourceType
from project_lens.main import create_app


def _headers() -> dict[str, str]:
    from tests.conftest import project_agent_headers

    return project_agent_headers(actor_id="u1", chat_id="chat-1", tenant_key="demo")


def _payload(*, fact_type: str, project_id: str = "payment") -> dict[str, object]:
    return {
        "tool_name": "projectlens_search_context",
        "project": {"tenant_id": "demo", "project_id": project_id},
        "user_id": "u1",
        "chat_id": "chat-1",
        "arguments": {
            "query": "当前项目事实",
            "fact_type": fact_type,
        },
    }


def _source(*, source_id: str, value: str, status: str = "published") -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        source_type=SourceType.FEISHU_DOCUMENT,
        project_id="payment",
        tenant_id="demo",
        title="ProjectLens pilot requirement",
        raw_uri=f"https://feishu.local/{source_id}",
        revision="r1",
        observed_at=datetime.now(timezone.utc),
        status=status,
        authority_scope=(FactType.REQUIREMENT_SCOPE.value,),
        access_scope="project:payment:read",
        content_hash=(source_id + value).encode().hex()[:64].ljust(16, "0"),
        content=f"requirement scope: {value}",
        fact_values={FactType.REQUIREMENT_SCOPE.value: value},
    )


def test_search_context_can_resolve_structured_fact_with_citations() -> None:
    app = create_app()
    app.state.source_record_store.put(_source(source_id="knowledge/req-scope", value="v1"))

    response = TestClient(app).post(
        "/api/v1/project-agent/tools/call",
        json=_payload(fact_type=FactType.REQUIREMENT_SCOPE.value),
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["mode"] == "fact_resolution"
    assert body["result"]["state"] == "confirmed"
    assert body["result"]["selected"]["source_id"] == "knowledge/req-scope"
    assert body["citations"]
    assert body["audit_ref"]["read_tool_names"]


def test_search_context_returns_unknown_when_fact_has_no_authorized_source() -> None:
    app = create_app()

    response = TestClient(app).post(
        "/api/v1/project-agent/tools/call",
        json=_payload(fact_type=FactType.TEST_STATUS.value, project_id="payment"),
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["mode"] == "fact_resolution"
    assert body["result"]["state"] == "unknown"
    assert body["result"]["selected"] is None
    assert body["unknowns"]


def test_search_context_marks_old_best_source_as_stale() -> None:
    app = create_app()
    old = _source(source_id="knowledge/old-scope", value="v0").model_copy(
        update={"observed_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    )
    app.state.source_record_store.put(old)

    response = TestClient(app).post(
        "/api/v1/project-agent/tools/call",
        json=_payload(fact_type=FactType.REQUIREMENT_SCOPE.value),
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["result"]["state"] == "stale"
    assert any("过期" in warning for warning in body["result"]["warnings"])
