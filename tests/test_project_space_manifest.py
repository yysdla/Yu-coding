from __future__ import annotations

import json
from pathlib import Path

from project_lens.project_space.registry import load_project_space_json
from project_lens.project_space.registry import load_project_spaces_from_dir
from project_lens.project_space.validation import (
    validate_project_space,
    validate_project_spaces,
)


ROOT = Path(__file__).resolve().parents[1]


def test_project_space_manifest_loads_namespaces_connectors_and_feishu_bindings(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    docs = tmp_path / "docs"
    repo.mkdir()
    docs.mkdir()
    path = tmp_path / "billing.json"
    path.write_text(
        json.dumps(
            {
                "tenant_id": "acme",
                "project_id": "billing",
                "display_name": "Billing",
                "repositories": [{"name": "billing-api", "path": "repo"}],
                "documents_root": "docs",
                "rag_namespace": "acme:billing:rag",
                "graph_namespace": "acme:billing:graph",
                "memory_namespace": "acme:billing:memory",
                "source_connectors": [
                    {
                        "kind": "repo",
                        "name": "billing-api",
                        "path": "repo",
                    },
                    {
                        "kind": "feishu_docs",
                        "name": "prd-docs",
                        "namespace": "acme:billing:feishu",
                        "metadata": {"token": "doc_xxx"},
                    },
                ],
                "feishu_chat_bindings": [
                    {
                        "tenant_key": "feishu-acme",
                        "chat_id": "oc_billing",
                        "visibility": "team_shared",
                    }
                ],
                "public_sources": ["knowledge/"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    space = load_project_space_json(path, base_dir=tmp_path)

    assert space.rag_namespace == "acme:billing:rag"
    assert space.graph_namespace == "acme:billing:graph"
    assert space.memory_namespace == "acme:billing:memory"
    assert space.source_connectors[0].kind == "repo"
    assert space.source_connectors[0].path == repo
    assert space.source_connectors[1].kind == "feishu_docs"
    assert space.source_connectors[1].metadata == {"token": "doc_xxx"}
    assert space.feishu_chat_bindings[0].chat_id == "oc_billing"


def test_project_space_validation_rejects_non_pluggable_or_unsafe_manifest(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broken.json"
    path.write_text(
        json.dumps(
            {
                "tenant_id": "acme",
                "project_id": "broken",
                "repositories": [{"name": "missing", "path": "missing-repo"}],
                "file_allowlist": ["src/", "../secrets/"],
                "rag_namespace": "",
                "graph_namespace": "",
                "memory_namespace": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    space = load_project_space_json(path, base_dir=tmp_path)
    report = validate_project_space(space)

    assert not report.ok
    assert report.has_code("missing_repository_path")
    assert report.has_code("unsafe_file_allowlist")
    assert report.has_code("missing_rag_namespace")
    assert report.has_code("missing_graph_namespace")
    assert report.has_code("missing_memory_namespace")


def test_project_space_validation_accepts_complete_pluggable_manifest(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    docs = tmp_path / "knowledge"
    repo.mkdir()
    docs.mkdir()

    path = tmp_path / "project.json"
    path.write_text(
        json.dumps(
            {
                "tenant_id": "acme",
                "project_id": "billing",
                "display_name": "Billing",
                "repositories": [{"name": "billing-api", "path": "repo"}],
                "documents_root": "knowledge",
                "file_allowlist": ["src/", "tests/", "knowledge/"],
                "public_sources": ["knowledge/"],
                "rag_namespace": "acme:billing:rag",
                "graph_namespace": "acme:billing:graph",
                "memory_namespace": "acme:billing:memory",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = validate_project_space(load_project_space_json(path, base_dir=tmp_path))

    assert report.ok
    assert report.errors == ()


def test_configured_project_spaces_are_valid_pluggable_manifests() -> None:
    spaces = load_project_spaces_from_dir(ROOT / "config" / "projects", base_dir=ROOT)

    reports = [validate_project_space(space) for space in spaces]

    assert spaces
    assert all(report.ok for report in reports), [
        (report.project_id, [(issue.code, issue.message) for issue in report.errors])
        for report in reports
    ]


def test_project_space_collection_validation_detects_cross_project_conflicts(
    tmp_path: Path,
) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    base = {
        "tenant_id": "acme",
        "repositories": [{"name": "repo", "path": "repo-a"}],
        "rag_namespace": "acme:shared:rag",
        "graph_namespace": "acme:shared:graph",
        "memory_namespace": "acme:shared:memory",
        "feishu_chat_bindings": [
            {"tenant_key": "feishu-acme", "chat_id": "oc_shared"}
        ],
    }
    first.write_text(
        json.dumps({**base, "project_id": "billing"}, ensure_ascii=False),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            {
                **base,
                "project_id": "crm",
                "repositories": [{"name": "repo", "path": "repo-b"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = validate_project_spaces(
        (
            load_project_space_json(first, base_dir=tmp_path),
            load_project_space_json(second, base_dir=tmp_path),
        )
    )

    assert not report.ok
    assert report.has_code("duplicate_feishu_chat_binding")
    assert report.has_code("duplicate_rag_namespace")
    assert report.has_code("duplicate_graph_namespace")
    assert report.has_code("duplicate_memory_namespace")
