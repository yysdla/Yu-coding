from datetime import datetime, timezone

from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext
from project_lens.context.store import InMemoryEvidenceIndex
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.graph.models import GraphNodeKind
from project_lens.graph.query import GraphQuery


def test_context_engine_query_graph_applies_acl_before_graph() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    evidence = Evidence(
        type=EvidenceType.INCIDENT,
        project=project,
        source=SourceRef(system="incident_archive", source_id="INC-ACL"),
        content="title: secret incident",
        observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        access_scope="project:payment:secret",
        content_hash="1234567890abcdef22",
    )
    index = InMemoryEvidenceIndex()
    index.add_many([evidence])
    engine = ContextEngine(index)

    denied = engine.query_graph(
        project,
        AccessContext(tenant_id="demo", user_id="u1", permissions=frozenset({"project:payment:read"})),
        GraphQuery(start_kind=GraphNodeKind.INCIDENT, end_kind=GraphNodeKind.SERVICE),
    )
    allowed = engine.query_graph(
        project,
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:secret"}),
        ),
        GraphQuery(start_kind=GraphNodeKind.INCIDENT, end_kind=GraphNodeKind.SERVICE),
    )
    assert denied == ()
    assert allowed
    assert allowed[0].evidence_ids


def test_context_engine_query_graph_endpoint_paths_stay_acl_filtered() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment", service="order-service")
    secret = Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="local_repository", source_id="src/order_service.py#L1-L8"),
        content="def create_order(): ...",
        observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        access_scope="project:payment:secret",
        content_hash="1234567890abcdef55",
        metadata={
            "endpoint_path": "/orders",
            "http_method": "POST",
            "file": "src/order_service.py",
            "symbol": "create_order",
            "symbol_type": "function",
        },
    )
    index = InMemoryEvidenceIndex()
    index.add_many([secret])
    engine = ContextEngine(index)
    query = GraphQuery(
        start_kind=GraphNodeKind.SERVICE,
        end_kind=GraphNodeKind.ENDPOINT,
        relation="exposes",
        max_depth=2,
    )

    denied = engine.query_graph(
        project,
        AccessContext(tenant_id="demo", user_id="u1", permissions=frozenset({"project:payment:read"})),
        query,
    )
    allowed = engine.query_graph(
        project,
        AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:secret"}),
        ),
        query,
    )

    assert denied == ()
    assert allowed
    assert allowed[0].cited_evidence == (secret,)
