from datetime import datetime, timezone

from project_lens.context.change_impact import build_change_impact
from project_lens.context.engine import ContextEngine
from project_lens.context.indexing.git_changes import GitChangeIndexer, ParsedCommit, parse_git_log
from project_lens.context.models import AccessContext, TimeRange
from project_lens.context.snapshot import build_project_snapshot
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.context.timeline import build_timeline_events
from project_lens.domain.models import EvidenceType, ProjectRef


def _project() -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )


def test_parse_git_log_builds_commit_metadata() -> None:
    raw = (
        "abc123\x1fAda\x1f2026-07-20T10:00:00+00:00\x1fparent1\x1f"
        "fix coupon null guard\x1fdetails\x1e\n"
        "src/order_service.py\n"
        "tests/test_order.py\n"
        "\x1e"
    )
    commits = parse_git_log(raw, branch="main")
    assert len(commits) == 1
    assert commits[0].sha == "abc123"
    assert commits[0].author == "Ada"
    assert commits[0].branch == "main"
    assert commits[0].files_changed == ("src/order_service.py", "tests/test_order.py")
    assert commits[0].parent_sha == "parent1"


def test_git_change_indexer_creates_commit_evidence() -> None:
    project = _project()
    commits = [
        ParsedCommit(
            sha="deadbeef01",
            author="Ada",
            committed_at=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
            subject="fix coupon null guard",
            body="restore optional coupon handling",
            branch="main",
            files_changed=("src/order_service.py",),
            insertions=3,
            deletions=1,
            parent_sha="cafebabe00",
        )
    ]
    evidence = GitChangeIndexer().index_parsed(
        commits,
        project=project,
        access_scope="project:payment:read",
    )
    assert len(evidence) == 1
    item = evidence[0]
    assert item.type == EvidenceType.COMMIT
    assert item.source.system == "local_git"
    assert item.metadata["commit_sha"] == "deadbeef01"
    assert item.metadata["author"] == "Ada"
    assert item.metadata["branch"] == "main"
    assert item.metadata["files_changed"] == ["src/order_service.py"]


def test_commit_evidence_enters_timeline_and_change_impact() -> None:
    project = _project()
    commits = [
        ParsedCommit(
            sha="deadbeef01",
            author="Ada",
            committed_at=datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
            subject="fix coupon null guard",
            body="restore optional coupon handling",
            branch="main",
            files_changed=("src/order_service.py",),
        )
    ]
    evidence = GitChangeIndexer().index_parsed(
        commits,
        project=project,
        access_scope="project:payment:read",
    )
    index = InMemoryEvidenceIndex()
    index.add_many(evidence)
    engine = ContextEngine(index)
    access = AccessContext(
        tenant_id="demo",
        user_id="u1",
        permissions=frozenset({"project:payment:read"}),
    )
    time_range = TimeRange(
        start=datetime(2026, 7, 19, tzinfo=timezone.utc),
        end=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    events = engine.timeline(project, access, time_range=time_range)
    impact = engine.change_impact(project, access, time_range=time_range)
    snapshot = build_project_snapshot(evidence, project=project)
    built_events = build_timeline_events(evidence, project=project)
    built_impact = build_change_impact(
        evidence,
        project=project,
        snapshot=snapshot,
        timeline=built_events,
    )

    assert any(event.event_type == "commit" for event in events)
    assert any(event.source.source_id == "deadbeef01" for event in events)
    assert impact.related_events
    assert evidence[0].id in impact.evidence_ids
    assert any(event.event_type == "commit" for event in built_impact.related_events)


def test_git_log_runner_can_be_faked_without_real_repo(tmp_path) -> None:
    project = _project()
    raw = (
        "abc123\x1fAda\x1f2026-07-20T10:00:00+00:00\x1f\x1f"
        "fix coupon null guard\x1f\x1e\n"
        "src/order_service.py\n"
    )

    def fake_runner(_root, args):
        if args[0] == "rev-parse":
            return "main\n"
        return raw

    evidence = GitChangeIndexer(log_runner=fake_runner).index(
        tmp_path,
        project=project,
        access_scope="project:payment:read",
        limit=5,
    )
    assert len(evidence) == 1
    assert evidence[0].metadata["branch"] == "main"
