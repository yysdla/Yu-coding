from datetime import datetime, timezone

from project_lens.context.indexing.git_changes import GitChangeIndexer, ParsedCommit
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.graph.builder import EvidenceGraphBuilder
from project_lens.graph.models import GraphNodeKind
from project_lens.graph.query import GraphQuery, GraphQueryService


def _project(project_id: str = "payment") -> ProjectRef:
    return ProjectRef(
        tenant_id="demo",
        project_id=project_id,
        service="order-service",
        environment="production",
    )


def _incident(project: ProjectRef) -> Evidence:
    return Evidence(
        type=EvidenceType.INCIDENT,
        project=project,
        source=SourceRef(system="incident_archive", source_id="INC-1"),
        content="title: checkout HTTP 500\nroot_cause: coupon null guard",
        observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef11",
        metadata={"owner_user_id": "ou_ada"},
    )


def test_graph_query_incident_to_service_path_has_evidence_ids() -> None:
    project = _project()
    evidence = (_incident(project),)
    results = GraphQueryService().query(
        evidence,
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.INCIDENT,
            end_kind=GraphNodeKind.SERVICE,
            relation="affects",
            max_depth=2,
        ),
    )
    assert results
    assert results[0].evidence_ids
    assert "order-service" in results[0].path_labels


def test_graph_query_commit_to_module_to_service_path() -> None:
    project = _project()
    commits = GitChangeIndexer().index_parsed(
        [
            ParsedCommit(
                sha="deadbeef01",
                author="Ada",
                committed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                subject="fix coupon null guard",
                body="",
                branch="main",
                files_changed=("src/order_service.py",),
            )
        ],
        project=project,
        access_scope="project:payment:read",
    )
    results = GraphQueryService().query(
        tuple(commits),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.COMMIT,
            end_kind=GraphNodeKind.SERVICE,
            max_depth=3,
        ),
    )
    assert results
    assert any("order-service" in item.path_labels for item in results)
    assert all(item.evidence_ids for item in results)


def test_graph_query_excludes_other_project_nodes() -> None:
    payment = _project("payment")
    other = _project("billing")
    evidence = (_incident(payment), _incident(other))
    graph = EvidenceGraphBuilder().build(evidence)
    assert any("billing" in node.id for node in graph.nodes)
    results = GraphQueryService().query(
        evidence,
        project=payment,
        query=GraphQuery(start_kind=GraphNodeKind.INCIDENT, end_kind=GraphNodeKind.SERVICE),
    )
    assert results
    assert all("billing" not in node_id for item in results for node_id in item.node_ids)


def test_graph_query_service_depends_on_service_path() -> None:
    from pathlib import Path

    from project_lens.context.indexing.dependencies import DependencyIndexer

    project = _project()
    demo = Path(__file__).parents[1] / "examples" / "payment_service"
    deps = DependencyIndexer().index_file(
        demo / "knowledge" / "dependencies.json",
        project=project,
        access_scope="project:payment:read",
    )
    assert deps
    graph = EvidenceGraphBuilder().build(tuple(deps))
    assert any(edge.relation == "depends_on" for edge in graph.edges)
    results = GraphQueryService().query(
        tuple(deps),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.SERVICE,
            end_kind=GraphNodeKind.SERVICE,
            relation="depends_on",
            max_depth=2,
        ),
    )
    assert results
    assert results[0].evidence_ids
    assert "order-service" in results[0].path_labels
    assert "payment-service" in results[0].path_labels


def test_graph_query_document_describes_module_with_cited_evidence() -> None:
    project = _project()
    doc = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="feishu_doc", source_id="docx_arch#chunk-0"),
        content="Order API runbook describes src/order_service.py",
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef33",
        metadata={
            "doc_token": "docx_arch",
            "title": "Order API runbook",
            "describes_modules": ("src/order_service.py",),
        },
    )

    results = GraphQueryService().query(
        (doc,),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.DOCUMENT,
            end_kind=GraphNodeKind.MODULE,
            relation="describes",
            max_depth=2,
        ),
    )

    assert results
    assert results[0].evidence_ids == (doc.id,)
    assert results[0].cited_evidence == (doc,)
    assert results[0].path_labels == ("Order API runbook", "src/order_service.py")


def test_graph_query_endpoint_links_service_module_and_symbol() -> None:
    project = _project()
    code = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local_repository", source_id="src/order_service.py#L10-L20"),
        content="def create_order(): ...",
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef44",
        metadata={
            "endpoint_path": "/orders",
            "http_method": "POST",
            "file": "src/order_service.py",
            "module": "src/order_service.py",
            "symbol": "create_order",
            "symbol_type": "function",
        },
    )

    service_to_endpoint = GraphQueryService().query(
        (code,),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.SERVICE,
            end_kind=GraphNodeKind.ENDPOINT,
            relation="exposes",
            max_depth=2,
        ),
    )
    endpoint_to_module = GraphQueryService().query(
        (code,),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.ENDPOINT,
            end_kind=GraphNodeKind.MODULE,
            relation="implemented_by",
            max_depth=2,
        ),
    )
    endpoint_to_symbol = GraphQueryService().query(
        (code,),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.ENDPOINT,
            end_kind=GraphNodeKind.SYMBOL,
            relation="implemented_by",
            max_depth=2,
        ),
    )

    assert service_to_endpoint
    assert endpoint_to_module
    assert endpoint_to_symbol
    assert service_to_endpoint[0].path_labels == ("order-service", "/orders")
    assert endpoint_to_module[0].path_labels == ("/orders", "src/order_service.py")
    assert endpoint_to_symbol[0].path_labels == ("/orders", "create_order")
    assert service_to_endpoint[0].cited_evidence == (code,)


def test_graph_query_document_describes_module_path_has_evidence_ids() -> None:
    project = _project()
    document = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="feishu_doc", source_id="docx_order_architecture"),
        content="Order architecture describes src/order_service.py checkout flow.",
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef44",
        metadata={
            "doc_token": "docx_order_architecture",
            "title": "Order Service Architecture",
            "describes_modules": ["src/order_service.py"],
        },
    )

    graph = EvidenceGraphBuilder().build((document,))
    assert any(node.kind == GraphNodeKind.DOCUMENT for node in graph.nodes)
    assert any(edge.relation == "describes" for edge in graph.edges)
    results = GraphQueryService().query(
        (document,),
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.DOCUMENT,
            end_kind=GraphNodeKind.MODULE,
            relation="describes",
            max_depth=2,
        ),
    )

    assert results
    assert results[0].evidence_ids == (document.id,)
    assert "Order Service Architecture" in results[0].path_labels
    assert "src/order_service.py" in results[0].path_labels


def test_graph_query_endpoint_to_module_and_service_paths() -> None:
    project = _project()
    code = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local_code", source_id="src/order_service.py:create_order"),
        content="def create_order(request): return checkout",
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef55",
        metadata={
            "file": "src/order_service.py",
            "module": "src/order_service.py",
            "symbol": "create_order",
            "endpoint_path": "/orders",
            "http_method": "POST",
        },
    )
    document = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="feishu_doc", source_id="docx_order_api"),
        content="POST /orders creates an order.",
        observed_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        access_scope="project:payment:read",
        content_hash="1234567890abcdef66",
        metadata={
            "doc_token": "docx_order_api",
            "title": "Order API",
            "endpoint_path": "/orders",
            "http_method": "POST",
            "describes_modules": ["src/order_service.py"],
        },
    )
    evidence = (code, document)

    graph = EvidenceGraphBuilder().build(evidence)
    assert any(node.kind == GraphNodeKind.ENDPOINT for node in graph.nodes)
    service_results = GraphQueryService().query(
        evidence,
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.SERVICE,
            end_kind=GraphNodeKind.ENDPOINT,
            relation="exposes",
            max_depth=2,
        ),
    )
    endpoint_module_results = GraphQueryService().query(
        evidence,
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.ENDPOINT,
            end_kind=GraphNodeKind.MODULE,
            relation="implemented_by",
            max_depth=2,
        ),
    )
    endpoint_symbol_results = GraphQueryService().query(
        evidence,
        project=project,
        query=GraphQuery(
            start_kind=GraphNodeKind.ENDPOINT,
            end_kind=GraphNodeKind.SYMBOL,
            relation="implemented_by",
            max_depth=2,
        ),
    )

    assert service_results
    assert any("/orders" in item.path_labels for item in service_results)
    assert endpoint_module_results
    assert any("src/order_service.py" in item.path_labels for item in endpoint_module_results)
    assert endpoint_symbol_results
    assert any("create_order" in item.path_labels for item in endpoint_symbol_results)
    assert all(item.evidence_ids for item in service_results + endpoint_module_results + endpoint_symbol_results)
