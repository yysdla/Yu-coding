from pathlib import Path

from project_lens.context.bootstrap import LocalContextSources, build_local_context_engine
from project_lens.context.engine import ContextEngine
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import ProjectRef


ACCESS_SCOPE = "project:payment:read"


def build_demo_engine(
    root: Path,
    *,
    include_git_and_feishu: bool = False,
) -> tuple[ContextEngine, InMemoryEvidenceIndex, ProjectRef]:
    project = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    sources = LocalContextSources(
        repository_root=root / "src",
        documents_root=root / "knowledge",
        incidents_file=root / "knowledge" / "incidents.json",
    )
    if include_git_and_feishu:
        sources = LocalContextSources(
            repository_root=root / "src",
            documents_root=root / "knowledge",
            incidents_file=root / "knowledge" / "incidents.json",
            git_log_file=root / "fixtures" / "git_log.txt",
            git_branch="main",
            feishu_documents_file=root / "knowledge" / "feishu_docs.json",
            tasks_file=root / "knowledge" / "tasks.json",
            releases_file=root / "knowledge" / "releases.json",
            dependencies_file=root / "knowledge" / "dependencies.json",
            ops_signals_file=root / "fixtures" / "ops_signals.json",
        )
    engine, index = build_local_context_engine(
        sources,
        project=project,
        access_scope=ACCESS_SCOPE,
    )
    return engine, index, project
