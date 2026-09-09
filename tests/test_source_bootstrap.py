import json
from pathlib import Path

from fastapi.testclient import TestClient

from project_lens.context.bootstrap import (
    LocalContextSources,
    LocalProjectRegistration,
    build_registered_context_engine,
    default_local_project_registrations,
    parse_local_project_registrations,
)
from project_lens.context.models import AccessContext, TimeRange
from project_lens.domain.models import EvidenceType, ProjectRef
from project_lens.main import create_app
from datetime import datetime, timezone


ROOT = Path(__file__).parents[1]
DEMO = ROOT / "examples" / "payment_service"
ACCESS = "project:payment:read"


def test_default_registration_indexes_git_fixture_and_feishu_docs() -> None:
    registrations = default_local_project_registrations(ROOT)
    payment = next(item for item in registrations if item.project.project_id == "payment")
    engine, index = build_registered_context_engine((payment,))
    project = payment.project
    evidence = index.all()
    types = {item.type for item in evidence}
    systems = {item.source.system for item in evidence}

    assert EvidenceType.COMMIT in types
    assert EvidenceType.TASK in types
    assert "local_git" in systems
    assert "feishu_doc" in systems
    assert "local_task" in systems
    assert "local_release" in systems
    assert any(item.metadata.get("commit_sha") == "a1b2c3d4e5f60718293a4b5c6d7e8f90" for item in evidence)
    assert any(item.metadata.get("owner_user_id") == "ou_ada" for item in evidence)

    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS}),
    )
    events = engine.timeline(
        project,
        access,
        time_range=TimeRange(
            start=datetime(2026, 7, 18, tzinfo=timezone.utc),
            end=datetime(2026, 7, 21, tzinfo=timezone.utc),
        ),
    )
    impact = engine.change_impact(project, access)
    report = engine.knowledge_gaps(project, access)

    assert any(event.event_type == "commit" for event in events)
    assert any(event.event_type == "task" for event in events)
    assert any(event.event_type == "release" for event in events)
    assert any(event.source.source_id.startswith("a1b2c3d4") for event in events)
    assert impact.related_events
    assert any(gap.source_signal == "missing_evidence_type:commit" for gap in report.gaps) is False
    assert any(gap.source_signal == "missing_evidence_type:task" for gap in report.gaps) is False
    assert report.signal_coverage.get("owner") == 1
    assert all(
        gap.target_ref != "service:order-service"
        for gap in report.gaps
        if gap.type.value == "owner"
    )


def test_registry_json_accepts_git_and_feishu_source_fields(tmp_path: Path) -> None:
    repo = tmp_path / "src"
    repo.mkdir()
    (repo / "svc.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    git_log = tmp_path / "git_log.txt"
    git_log.write_bytes(
        (
            b"abc123\x1fAda\x1f2026-07-20T10:00:00+00:00\x1f\x1f"
            b"fix coupon null guard\x1f\x1e\n"
            b"src/order_service.py\n"
            b"\x1e\n"
        )
    )
    feishu = tmp_path / "feishu_docs.json"
    feishu.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "tenant_id": "demo",
                        "project_id": "payment",
                        "doc_token": "doc-1",
                        "doc_url": "https://feishu.example/docx/doc-1",
                        "title": "Owners",
                        "content": "order-service owner is Ada",
                        "revision": "r1",
                        "updated_at": "2026-07-20T12:00:00+00:00",
                        "owner_user_id": "ou_ada",
                        "access_scope": "project:payment:read",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    raw = json.dumps(
        {
            "projects": [
                {
                    "tenant_id": "demo",
                    "project_id": "payment",
                    "repository_root": str(repo),
                    "git_log_file": str(git_log),
                    "git_branch": "main",
                    "feishu_documents_file": str(feishu),
                    "access_scope": "project:payment:read",
                }
            ]
        }
    )
    registrations = parse_local_project_registrations(raw, base_dir=tmp_path)
    engine, index = build_registered_context_engine(registrations)
    evidence = index.all()
    assert any(item.type == EvidenceType.COMMIT for item in evidence)
    assert any(item.source.system == "feishu_doc" for item in evidence)
    report = engine.knowledge_gaps(
        ProjectRef(tenant_id="demo", project_id="payment"),
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:read"}),
        ),
    )
    assert all(gap.type.value != "owner" for gap in report.gaps)


def test_app_timeline_and_gaps_use_bootstrapped_git_and_feishu_sources() -> None:
    client = TestClient(create_app())
    project = {
        "tenant_id": "demo",
        "project_id": "payment",
        "service": "order-service",
        "environment": "production",
    }
    timeline = client.post(
        "/api/v1/projects/timeline",
        json={
            "project": project,
            "user_id": "u1",
            "permissions": [ACCESS],
            "limit": 20,
        },
    )
    gaps = client.post(
        "/api/v1/projects/knowledge-gaps",
        json={
            "project": project,
            "user_id": "u1",
            "permissions": [ACCESS],
        },
    )
    impact = client.post(
        "/api/v1/projects/change-impact",
        json={
            "project": project,
            "user_id": "u1",
            "permissions": [ACCESS],
            "limit": 20,
        },
    )

    assert timeline.status_code == 200
    event_types = {event["event_type"] for event in timeline.json()["events"]}
    # App bootstrap follows config/projects (code/docs). Richer git/task/release
    # indexing is covered by default_local_project_registrations unit tests.
    assert {"code", "document"} <= event_types
    assert gaps.status_code == 200
    report = gaps.json()
    assert report["type_coverage"].get("code", 0) > 0
    assert report["type_coverage"].get("document", 0) > 0
    assert impact.status_code == 200
    assert impact.json()["related_events"] is not None


def test_build_registered_engine_with_explicit_sources() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    _, index = build_registered_context_engine(
        (
            LocalProjectRegistration(
                project=project,
                sources=LocalContextSources(
                    repository_root=DEMO / "src",
                    documents_root=DEMO / "knowledge",
                    incidents_file=DEMO / "knowledge" / "incidents.json",
                    git_log_file=DEMO / "fixtures" / "git_log.txt",
                    git_branch="main",
                    feishu_documents_file=DEMO / "knowledge" / "feishu_docs.json",
                    tasks_file=DEMO / "knowledge" / "tasks.json",
                    releases_file=DEMO / "knowledge" / "releases.json",
                ),
                access_scope=ACCESS,
            ),
        )
    )
    assert any(item.source.system == "local_git" for item in index.all())
    assert any(item.source.system == "feishu_doc" for item in index.all())
    assert any(item.source.system == "local_task" for item in index.all())
    assert any(item.source.system == "local_release" for item in index.all())
