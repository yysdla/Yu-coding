from datetime import datetime, timezone
from pathlib import Path

from project_lens.context.bootstrap import (
    LocalContextSources,
    LocalProjectRegistration,
    build_registered_context_engine,
)
from project_lens.context.indexing.tasks import TaskIndexer
from project_lens.context.models import AccessContext, TimeRange
from project_lens.context.sources.tasks import ReleaseRecord, TaskRecord
from project_lens.domain.models import EvidenceType, ProjectRef
from project_lens.graph.builder import EvidenceGraphBuilder
from project_lens.graph.models import GraphNodeKind
from project_lens.graph.query import GraphQuery, GraphQueryService

DEMO = Path(__file__).parents[1] / "examples" / "payment_service"
ACCESS = "project:payment:read"


def test_task_and_release_files_index_as_task_evidence() -> None:
    indexer = TaskIndexer()
    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    tasks = indexer.index_tasks_file(DEMO / "knowledge" / "tasks.json", project=project)
    releases = indexer.index_releases_file(
        DEMO / "knowledge" / "releases.json", project=project
    )

    assert len(tasks) == 1
    assert tasks[0].type == EvidenceType.TASK
    assert tasks[0].source.system == "local_task"
    assert tasks[0].metadata["kind"] == "task"
    assert tasks[0].metadata["related_incident_id"] == "INC-2026-001"
    assert "null guard" in tasks[0].content

    assert len(releases) == 1
    assert releases[0].type == EvidenceType.TASK
    assert releases[0].source.system == "local_release"
    assert releases[0].metadata["kind"] == "release"
    assert releases[0].metadata["version"] == "2026.07.20"
    assert "latest release" in releases[0].content.lower()


def test_empty_or_wrong_project_tasks_are_skipped() -> None:
    indexer = TaskIndexer()
    project = ProjectRef(tenant_id="demo", project_id="payment")
    evidence = indexer.index_tasks(
        [
            TaskRecord(
                tenant_id="demo",
                project_id="other",
                task_id="T-1",
                title="Ignored",
                summary="other project",
                updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                access_scope=ACCESS,
            ),
            TaskRecord(
                tenant_id="demo",
                project_id="payment",
                task_id="T-2",
                title="Empty",
                summary="   ",
                updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                access_scope=ACCESS,
            ),
        ],
        project=project,
    )
    assert evidence == []


def test_task_status_change_updates_content_hash() -> None:
    indexer = TaskIndexer()
    project = ProjectRef(tenant_id="demo", project_id="payment")
    base = TaskRecord(
        tenant_id="demo",
        project_id="payment",
        task_id="T-hash",
        title="Fix checkout",
        summary="restore null guard",
        status="open",
        updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        access_scope=ACCESS,
    )
    first = indexer.index_tasks([base], project=project)[0]
    second = indexer.index_tasks(
        [base.model_copy(update={"status": "done"})],
        project=project,
    )[0]
    assert first.content_hash != second.content_hash


def test_bootstrap_tasks_and_releases_feed_timeline_and_gaps() -> None:
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    engine, index = build_registered_context_engine(
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
    evidence = index.all()
    assert any(item.source.system == "local_task" for item in evidence)
    assert any(item.source.system == "local_release" for item in evidence)

    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({ACCESS}),
    )
    events = engine.timeline(
        project,
        access,
        time_range=TimeRange(
            start=datetime(2026, 7, 19, tzinfo=timezone.utc),
            end=datetime(2026, 7, 21, tzinfo=timezone.utc),
        ),
    )
    report = engine.knowledge_gaps(project, access)
    impact = engine.change_impact(project, access)

    assert any(event.event_type == "task" for event in events)
    assert any(event.event_type == "release" for event in events)
    assert any(gap.type.value == "task_tracking" for gap in report.gaps) is False
    assert any(event.event_type in {"task", "release"} for event in impact.related_events)


def test_graph_task_tracks_incident_and_release_includes_commit() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    indexer = TaskIndexer()
    task = indexer.index_tasks(
        [
            TaskRecord(
                tenant_id="demo",
                project_id="payment",
                task_id="TASK-1",
                title="Track checkout outage",
                summary="Track INC-1 after release",
                updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                access_scope=ACCESS,
                service="order-service",
                related_incident_id="INC-1",
                related_commit_sha="deadbeef01",
            )
        ],
        project=project,
    )[0]
    release = indexer.index_releases(
        [
            ReleaseRecord(
                tenant_id="demo",
                project_id="payment",
                release_id="REL-1",
                version="1.2.3",
                title="Hotfix",
                summary="latest release includes deadbeef01",
                released_at=datetime(2026, 7, 20, 15, tzinfo=timezone.utc),
                access_scope=ACCESS,
                service="order-service",
                commit_shas=("deadbeef01",),
            )
        ],
        project=project,
    )[0]
    graph = EvidenceGraphBuilder().build((task, release))
    kinds = {node.kind for node in graph.nodes}
    assert GraphNodeKind.TASK in kinds
    assert GraphNodeKind.RELEASE in kinds
    assert GraphNodeKind.INCIDENT in kinds
    assert GraphNodeKind.COMMIT in kinds
    assert any(edge.relation == "tracks" for edge in graph.edges)
    assert any(edge.relation == "includes" for edge in graph.edges)
    assert any(edge.relation == "linked_to" for edge in graph.edges)

    results = GraphQueryService().query(
        (task, release),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.TASK,
            end_kind=GraphNodeKind.INCIDENT,
            relation="tracks",
            max_depth=2,
        ),
    )
    assert results
    assert results[0].evidence_ids


def test_graph_incident_caused_by_related_commit() -> None:
    from pathlib import Path

    from project_lens.context.indexing.documents import IncidentIndexer

    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    demo = Path(__file__).parents[1] / "examples" / "payment_service"
    incidents = IncidentIndexer().index_file(
        demo / "knowledge" / "incidents.json",
        project=project,
        access_scope=ACCESS,
    )
    assert incidents
    assert incidents[0].metadata.get("related_commit_sha") == (
        "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    )
    graph = EvidenceGraphBuilder().build(tuple(incidents))
    caused = [
        edge
        for edge in graph.edges
        if edge.relation == "caused_by"
        and edge.source.startswith("incident:")
        and edge.target.startswith("commit:")
    ]
    assert caused
    assert "INC-2026-001" in caused[0].source
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" in caused[0].target

    results = GraphQueryService().query(
        tuple(incidents),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.INCIDENT,
            end_kind=GraphNodeKind.COMMIT,
            relation="caused_by",
            max_depth=2,
        ),
    )
    assert results
    assert results[0].evidence_ids
