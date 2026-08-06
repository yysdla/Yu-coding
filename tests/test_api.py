from fastapi.testclient import TestClient

from project_lens.main import create_app


TRACEBACK = """Traceback (most recent call last):
  File "order_service.py", line 16, in create_order
    coupon_id = request.coupon.id
AttributeError: 'NoneType' object has no attribute 'id'
"""


def test_health() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_and_get_run() -> None:
    client = TestClient(create_app())
    create_response = client.post(
        "/api/v1/runs",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "feishu-user-1",
            "channel_id": "feishu-group-1",
            "question": "Why did payment requests fail?",
        },
    )

    assert create_response.status_code == 202
    run_id = create_response.json()["run_id"]
    get_response = client.get(f"/api/v1/runs/{run_id}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "accepted"
    assert get_response.json()["question"] == "Why did payment requests fail?"


def test_rejects_unknown_request_fields() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/runs",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "question": "status?",
            "unexpected": True,
        },
    )

    assert response.status_code == 422


def test_project_snapshot_endpoint_returns_authorized_project_map() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/snapshot",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "permissions": ["project:payment:read"],
        },
    )

    assert response.status_code == 200
    snapshot = response.json()
    assert "order-service" in snapshot["services"]
    assert "create_order" in snapshot["entrypoints"]
    assert "payment service" in snapshot["dependencies"]
    assert snapshot["evidence_ids"]


def test_project_snapshot_endpoint_respects_permissions() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/snapshot",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "permissions": [],
        },
    )

    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["evidence_ids"] == []
    assert "缺少服务归属证据。" in snapshot["unresolved_items"]


def test_project_timeline_endpoint_returns_authorized_events() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/timeline",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "permissions": ["project:payment:read"],
            "limit": 5,
        },
    )

    assert response.status_code == 200
    events = response.json()["events"]
    assert events
    assert any(event["event_type"] == "incident" for event in events)
    assert any(event["source"]["source_id"] == "INC-2026-001" for event in events)


def test_project_timeline_endpoint_respects_permissions() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/timeline",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "permissions": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["events"] == []


def test_project_change_impact_endpoint_returns_authorized_summary() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/change-impact",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "permissions": ["project:payment:read"],
            "limit": 5,
        },
    )

    assert response.status_code == 200
    impact = response.json()
    assert impact["related_events"]
    assert impact["evidence_ids"]
    assert "Observed" in impact["summary"]


def test_project_change_impact_endpoint_returns_empty_without_permission() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/change-impact",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "permissions": [],
        },
    )

    assert response.status_code == 200
    impact = response.json()
    assert impact["related_events"] == []
    assert impact["evidence_ids"] == []


def test_project_knowledge_gaps_endpoint_returns_structured_report() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/knowledge-gaps",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "u1",
            "permissions": ["project:payment:read"],
        },
    )

    assert response.status_code == 200
    report = response.json()
    assert report["evidence_ids"]
    assert report["type_coverage"]["code"] > 0
    assert report["type_coverage"].get("commit", 0) > 0
    assert report["type_coverage"].get("task", 0) > 0
    assert all(gap["type"] != "task_tracking" for gap in report["gaps"])
    assert all("source_signal" in gap for gap in report["gaps"])
    assert report.get("signal_coverage", {}).get("owner") == 1
    # Primary service is owned; dependency services may still surface graph owner gaps.
    assert all(
        gap.get("target_ref") != "service:order-service"
        for gap in report["gaps"]
        if gap["type"] == "owner"
    )


def test_project_knowledge_gaps_endpoint_respects_permissions() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/knowledge-gaps",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "user_id": "u1",
            "permissions": [],
        },
    )

    assert response.status_code == 200
    report = response.json()
    assert report["evidence_ids"] == []
    assert report["gaps"]


def test_execute_error_analysis_and_query_events() -> None:
    client = TestClient(create_app())
    create_response = client.post(
        "/api/v1/runs",
        json={
            "project": {
                "tenant_id": "demo",
                "project_id": "payment",
                "service": "order-service",
                "environment": "production",
            },
            "user_id": "feishu-user-1",
            "channel_id": "feishu-group-1",
            "question": TRACEBACK,
        },
    )
    run_id = create_response.json()["run_id"]

    execute_response = client.post(f"/api/v1/runs/{run_id}/execute")

    assert execute_response.status_code == 200
    run = execute_response.json()
    assert run["status"] == "completed"
    assert run["answer"]["skill"] == "incident_diagnosis"
    assert 0.0 <= run["answer"]["confidence"] <= 1.0
    assert "项目资料" in run["answer"]["business_summary"]
    assert "order_service.py#L14-L20" in run["answer"]["technical_summary"]
    assert len(run["answer"]["evidence"]) >= 2
    events_response = client.get(f"/api/v1/runs/{run_id}/events")
    assert events_response.status_code == 200
    events = events_response.json()
    statuses = [
        event["payload"]["status"]
        for event in events
        if event["type"] == "run_status_changed"
    ]
    assert statuses == ["resolving", "collecting", "analyzing", "verifying"]
    skill_events = [
        event["payload"]["skill"]
        for event in events
        if event["type"] == "run_status_changed" and "skill" in event["payload"]
    ]
    assert skill_events == ["incident_diagnosis", "incident_diagnosis"]
    assert events[-1]["type"] == "run_completed"


def test_unknown_project_execution_is_recorded_as_failed() -> None:
    client = TestClient(create_app())
    create_response = client.post(
        "/api/v1/runs",
        json={
            "project": {"tenant_id": "demo", "project_id": "unknown"},
            "user_id": "u1",
            "question": "What failed?",
        },
    )
    run_id = create_response.json()["run_id"]

    execute_response = client.post(f"/api/v1/runs/{run_id}/execute")

    assert execute_response.status_code == 200
    assert execute_response.json()["status"] == "failed"
    assert "not registered" in execute_response.json()["error"]
    events = client.get(f"/api/v1/runs/{run_id}/events").json()
    assert events[-1]["type"] == "run_failed"
