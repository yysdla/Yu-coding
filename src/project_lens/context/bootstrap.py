"""Helpers for constructing a local context engine from project artifacts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_lens.context.engine import ContextEngine
from project_lens.context.indexing import (
    DependencyIndexer,
    DocumentIndexer,
    FeishuDocumentIndexer,
    GitChangeIndexer,
    IncidentIndexer,
    PythonCodeIndexer,
    TaskIndexer,
)
from project_lens.context.ops.query import OpsQueryService
from project_lens.context.ops.store import InMemoryOpsSignalStore, load_ops_signals_file
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, ProjectRef
from project_lens.workflow.models import ProjectRegistration


@dataclass(frozen=True)
class LocalContextSources:
    repository_root: Path
    documents_root: Path | None = None
    incidents_file: Path | None = None
    git_root: Path | None = None
    git_log_file: Path | None = None
    git_branch: str | None = None
    git_limit: int = 50
    feishu_documents_file: Path | None = None
    feishu_doc_tokens: tuple[str, ...] = ()
    tasks_file: Path | None = None
    releases_file: Path | None = None
    dependencies_file: Path | None = None
    ops_signals_file: Path | None = None


@dataclass(frozen=True)
class LocalProjectRegistration:
    project: ProjectRef
    sources: LocalContextSources
    access_scope: str


def build_local_context_engine(
    sources: LocalContextSources,
    *,
    project: ProjectRef,
    access_scope: str,
) -> tuple[ContextEngine, InMemoryEvidenceIndex]:
    return build_registered_context_engine(
        (
            LocalProjectRegistration(
                project=project,
                sources=sources,
                access_scope=access_scope,
            ),
        )
    )


def build_registered_context_engine(
    registrations: tuple[LocalProjectRegistration, ...],
) -> tuple[ContextEngine, InMemoryEvidenceIndex]:
    _validate_registrations(registrations)
    index = InMemoryEvidenceIndex()
    code_indexer = PythonCodeIndexer()
    document_indexer = DocumentIndexer()
    incident_indexer = IncidentIndexer()
    git_indexer = GitChangeIndexer()
    feishu_indexer = FeishuDocumentIndexer()
    task_indexer = TaskIndexer()
    dependency_indexer = DependencyIndexer()
    ops_store = InMemoryOpsSignalStore()

    for registration in registrations:
        sources = registration.sources
        index.add_many(
            code_indexer.index(
                sources.repository_root,
                project=registration.project,
                access_scope=registration.access_scope,
            )
        )
        if sources.documents_root:
            index.add_many(
                document_indexer.index(
                    sources.documents_root,
                    project=registration.project,
                    access_scope=registration.access_scope,
                )
            )
        if sources.incidents_file:
            index.add_many(
                incident_indexer.index_file(
                    sources.incidents_file,
                    project=registration.project,
                    access_scope=registration.access_scope,
                )
            )
        index.add_many(
            _index_git_changes(
                git_indexer,
                sources,
                project=registration.project,
                access_scope=registration.access_scope,
            )
        )
        if sources.feishu_documents_file is not None:
            index.add_many(
                feishu_indexer.index_file(
                    sources.feishu_documents_file,
                    project=registration.project,
                )
            )
        if sources.tasks_file is not None:
            index.add_many(
                task_indexer.index_tasks_file(
                    sources.tasks_file,
                    project=registration.project,
                )
            )
        if sources.releases_file is not None:
            index.add_many(
                task_indexer.index_releases_file(
                    sources.releases_file,
                    project=registration.project,
                )
            )
        if sources.dependencies_file is not None:
            index.add_many(
                dependency_indexer.index_file(
                    sources.dependencies_file,
                    project=registration.project,
                    access_scope=registration.access_scope,
                )
            )
        if sources.ops_signals_file is not None:
            ops_store.add_many(load_ops_signals_file(sources.ops_signals_file))
    return ContextEngine(index, ops_service=OpsQueryService(ops_store)), index


def _index_git_changes(
    git_indexer: GitChangeIndexer,
    sources: LocalContextSources,
    *,
    project: ProjectRef,
    access_scope: str,
) -> list[Evidence]:
    if sources.git_log_file is not None:
        return git_indexer.index_log_file(
            sources.git_log_file,
            project=project,
            access_scope=access_scope,
            branch=sources.git_branch,
            limit=sources.git_limit,
        )
    if sources.git_root is None:
        return []
    try:
        return git_indexer.index(
            sources.git_root,
            project=project,
            access_scope=access_scope,
            branch=sources.git_branch,
            limit=sources.git_limit,
        )
    except (OSError, ValueError, subprocess.CalledProcessError):
        return []


def _validate_registrations(registrations: tuple[LocalProjectRegistration, ...]) -> None:
    if not registrations:
        raise ValueError("at least one local project registration is required")
    projects = [item.project for item in registrations]
    if len(set(projects)) != len(projects):
        raise ValueError("duplicate local project registration")


def default_local_project_registrations(base_dir: Path) -> tuple[LocalProjectRegistration, ...]:
    """Default indexed projects: ProjectLens first, payment/crm remain demos."""

    projectlens = ProjectRef(
        tenant_id="demo",
        project_id="projectlens",
        service="project-lens-api",
        environment="local",
    )
    payment = ProjectRef(
        tenant_id="demo",
        project_id="payment",
        service="order-service",
        environment="production",
    )
    crm = ProjectRef(
        tenant_id="demo",
        project_id="crm",
        service="contact-service",
        environment="production",
    )
    payment_root = base_dir / "examples" / "payment_service"
    crm_root = base_dir / "examples" / "crm_service"
    return (
        LocalProjectRegistration(
            project=projectlens,
            sources=LocalContextSources(
                repository_root=base_dir / "src",
                documents_root=base_dir / "knowledge",
                git_root=base_dir,
                git_branch="main",
                git_limit=40,
            ),
            access_scope="project:projectlens:read",
        ),
        LocalProjectRegistration(
            project=payment,
            sources=LocalContextSources(
                repository_root=payment_root / "src",
                documents_root=payment_root / "knowledge",
                incidents_file=payment_root / "knowledge" / "incidents.json",
                git_log_file=payment_root / "fixtures" / "git_log.txt",
                git_branch="main",
                feishu_documents_file=payment_root / "knowledge" / "feishu_docs.json",
                tasks_file=payment_root / "knowledge" / "tasks.json",
                releases_file=payment_root / "knowledge" / "releases.json",
                dependencies_file=payment_root / "knowledge" / "dependencies.json",
                ops_signals_file=payment_root / "fixtures" / "ops_signals.json",
            ),
            access_scope="project:payment:read",
        ),
        LocalProjectRegistration(
            project=crm,
            sources=LocalContextSources(
                repository_root=crm_root / "src",
                documents_root=crm_root / "knowledge",
                git_branch="main",
            ),
            access_scope="project:crm:read",
        ),
    )


def parse_local_project_registrations(
    raw: str,
    *,
    base_dir: Path,
) -> tuple[LocalProjectRegistration, ...]:
    if not raw.strip():
        return default_local_project_registrations(base_dir)

    payload: dict[str, Any] = json.loads(raw)
    registrations: list[LocalProjectRegistration] = []
    for item in payload.get("projects", []):
        project = ProjectRef(
            tenant_id=str(item["tenant_id"]),
            project_id=str(item["project_id"]),
            service=_optional_str(item.get("service")),
            environment=_optional_str(item.get("environment")),
        )
        registrations.append(
            LocalProjectRegistration(
                project=project,
                sources=LocalContextSources(
                    repository_root=_resolve_path(base_dir, item["repository_root"]),
                    documents_root=_resolve_optional_path(base_dir, item.get("documents_root")),
                    incidents_file=_resolve_optional_path(base_dir, item.get("incidents_file")),
                    git_root=_resolve_optional_path(base_dir, item.get("git_root")),
                    git_log_file=_resolve_optional_path(base_dir, item.get("git_log_file")),
                    git_branch=_optional_str(item.get("git_branch")),
                    git_limit=int(item.get("git_limit", 50)),
                    feishu_documents_file=_resolve_optional_path(
                        base_dir, item.get("feishu_documents_file")
                    ),
                    feishu_doc_tokens=_string_tuple(item.get("feishu_doc_tokens")),
                    tasks_file=_resolve_optional_path(base_dir, item.get("tasks_file")),
                    releases_file=_resolve_optional_path(base_dir, item.get("releases_file")),
                    dependencies_file=_resolve_optional_path(
                        base_dir, item.get("dependencies_file")
                    ),
                    ops_signals_file=_resolve_optional_path(
                        base_dir, item.get("ops_signals_file")
                    ),
                ),
                access_scope=str(
                    item.get("access_scope") or f"project:{project.project_id}:read"
                ),
            )
        )
    if not registrations:
        raise ValueError("local project registry must contain at least one project")
    return tuple(registrations)


def to_project_registrations(
    registrations: tuple[LocalProjectRegistration, ...],
) -> tuple[ProjectRegistration, ...]:
    return tuple(
        ProjectRegistration(project=item.project, access_scope=item.access_scope)
        for item in registrations
    )


def _resolve_path(base_dir: Path, raw: object) -> Path:
    path = Path(str(raw))
    if path.is_absolute():
        return path
    return base_dir / path


def _resolve_optional_path(base_dir: Path, raw: object | None) -> Path | None:
    if raw in (None, ""):
        return None
    return _resolve_path(base_dir, raw)


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_tuple(value: object | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("feishu_doc_tokens must be a JSON array of strings")
    tokens: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("feishu_doc_tokens entries must be non-empty strings")
        tokens.append(item.strip())
    return tuple(dict.fromkeys(tokens))
