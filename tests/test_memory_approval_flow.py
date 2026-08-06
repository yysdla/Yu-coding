from uuid import uuid4

from fastapi.testclient import TestClient

from project_lens.main import create_app


def test_memory_proposal_api_requires_approval_before_memory() -> None:
    client = TestClient(create_app())
    evidence_id = str(uuid4())
    create = client.post(
        "/api/v1/projects/memory-proposals",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "proposed_by": "u1",
            "claim_text": "order-service owner is Ada",
            "evidence_ids": [evidence_id],
            "reason": "from architecture evidence",
        },
    )
    assert create.status_code == 201
    proposal = create.json()
    assert proposal["status"] == "pending"

    listed_before = client.get("/api/v1/projects/demo/payment/memories")
    assert listed_before.status_code == 200
    assert listed_before.json() == []

    rejected = client.post(
        f"/api/v1/memory-proposals/{proposal['id']}/decision",
        json={"approved": False, "decided_by": "lead"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["memory"] is None

    # cannot decide twice
    conflict = client.post(
        f"/api/v1/memory-proposals/{proposal['id']}/decision",
        json={"approved": True, "decided_by": "lead"},
    )
    assert conflict.status_code == 409

    second = client.post(
        "/api/v1/projects/memory-proposals",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "proposed_by": "u1",
            "claim_text": "order-service owner is Ada",
            "evidence_ids": [evidence_id],
        },
    ).json()
    approved = client.post(
        f"/api/v1/memory-proposals/{second['id']}/decision",
        json={"approved": True, "decided_by": "lead"},
    )
    assert approved.status_code == 200
    memory = approved.json()["memory"]
    assert memory["approved_by"] == "lead"
    assert memory["evidence_ids"] == [evidence_id]
    assert memory["valid_from"]

    listed = client.get("/api/v1/projects/demo/payment/memories")
    assert len(listed.json()) == 1


def test_memory_proposal_without_evidence_is_rejected() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/projects/memory-proposals",
        json={
            "project": {"tenant_id": "demo", "project_id": "payment"},
            "proposed_by": "u1",
            "claim_text": "unsupported claim",
            "evidence_ids": [],
        },
    )
    assert response.status_code == 400
