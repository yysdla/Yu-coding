"""query_graph must put GraphEvidence.cited_evidence into InvestigationLedger."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

import pytest

from project_lens.agent.read_tools import InvestigationLedger, QueryGraphTool
from project_lens.context.models import AccessContext
from project_lens.domain.models import Evidence, EvidenceType, GraphEvidence, ProjectRef, SourceRef
from project_lens.graph.models import GraphNodeKind
from project_lens.graph.query import GraphQuery


class _FakeGateway:
    def __init__(self, paths: tuple[GraphEvidence, ...]) -> None:
        self._paths = paths

    def query_graph(
        self,
        project: ProjectRef,
        access: AccessContext,
        query: GraphQuery,
        *,
        scope=None,  # noqa: ANN001
    ) -> tuple[GraphEvidence, ...]:
        del project, access, query, scope
        return self._paths


@pytest.mark.asyncio
async def test_query_graph_tool_adds_cited_evidence_to_ledger() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    body = "architecture describes order_service"
    digest = sha256(body.encode("utf-8")).hexdigest()[:32]
    cited = Evidence(
        type=EvidenceType.DOCUMENT,
        project=project,
        source=SourceRef(system="knowledge", source_id="architecture.md"),
        content=body,
        observed_at=datetime.now(timezone.utc),
        access_scope="project:payment:read",
        content_hash=digest,
    )
    path = GraphEvidence(
        project=project,
        path_labels=("architecture.md", "order_service"),
        relations=("describes",),
        node_ids=("doc:architecture", "module:order"),
        evidence_ids=(cited.id,),
        summary="architecture.md -> order_service",
        cited_evidence=(cited,),
    )
    ledger = InvestigationLedger()
    tool = QueryGraphTool(
        project=project,
        access=AccessContext(
            tenant_id="demo",
            user_id="u1",
            permissions=frozenset({"project:payment:read"}),
        ),
        gateway=_FakeGateway((path,)),  # type: ignore[arg-type]
        ledger=ledger,
    )
    raw = await tool.execute(start_kind=GraphNodeKind.DOCUMENT.value, limit=5)
    assert cited.id in ledger.evidence
    assert "query_graph" in ledger.tool_names
    assert str(cited.id) in raw
