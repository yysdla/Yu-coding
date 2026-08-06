from pathlib import Path

from project_lens.context.indexing import DocumentIndexer, IncidentIndexer, PythonCodeIndexer
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import EvidenceType, ProjectRef


def project() -> ProjectRef:
    return ProjectRef(tenant_id="demo", project_id="payment", service="order-service")


def test_python_indexer_preserves_symbol_and_lines(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        '"""Service module."""\n\nimport json\n\ndef create_order(value: str) -> str:\n'
        "    return value\n",
        encoding="utf-8",
    )

    evidence = PythonCodeIndexer().index(
        tmp_path,
        project=project(),
        access_scope="project:payment:read",
    )

    function = next(item for item in evidence if item.metadata.get("symbol") == "create_order")
    assert function.type == EvidenceType.CODE
    assert function.metadata["start_line"] == 5
    assert function.metadata["imports"] == ("json",)
    assert function.source.source_id.startswith("service.py#L5-")


def test_python_indexer_extracts_route_hint_from_function_docstring(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        "def create_order(value: str) -> str:\n"
        "    \"\"\"Create an order.\n\n"
        "    route: POST /orders\n"
        "    \"\"\"\n"
        "    return value\n",
        encoding="utf-8",
    )

    evidence = PythonCodeIndexer().index(
        tmp_path,
        project=project(),
        access_scope="project:payment:read",
    )

    function = next(item for item in evidence if item.metadata.get("symbol") == "create_order")
    assert function.metadata["endpoint_path"] == "/orders"
    assert function.metadata["http_method"] == "POST"


def test_document_and_incident_indexers(tmp_path: Path) -> None:
    (tmp_path / "runbook.md").write_text(
        "# Runbook\n\nRestore the coupon null guard after confirming the traceback.",
        encoding="utf-8",
    )
    incident_file = tmp_path / "incidents.json"
    incident_file.write_text(
        '[{"id":"INC-1","title":"Coupon failure","root_cause":"Missing null guard",'
        '"service":"order-service","occurred_at":"2026-07-20T10:00:00+00:00"}]',
        encoding="utf-8",
    )

    documents = DocumentIndexer().index(
        tmp_path,
        project=project(),
        access_scope="project:payment:read",
    )
    incidents = IncidentIndexer().index_file(
        incident_file,
        project=project(),
        access_scope="project:payment:read",
    )

    assert documents[0].type == EvidenceType.DOCUMENT
    assert incidents[0].type == EvidenceType.INCIDENT
    assert incidents[0].source.source_id == "INC-1"


def test_index_deduplicates_same_source_version(tmp_path: Path) -> None:
    path = tmp_path / "readme.md"
    path.write_text("Coupon handling documentation.", encoding="utf-8")
    evidence = DocumentIndexer().index(
        tmp_path,
        project=project(),
        access_scope="project:payment:read",
    )
    index = InMemoryEvidenceIndex()

    assert index.add_many(evidence) == 1
    assert index.add_many(evidence) == 0
    assert len(index) == 1
