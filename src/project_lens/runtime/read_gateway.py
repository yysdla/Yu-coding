"""Read-lane ContextEngine facade with tool-spec gates and audit.

Workflow must collect project knowledge through this gateway (or ToolGateway for
engineering file reads). Feishu adapters must not call ContextEngine / EvidenceIndex.
Apply remains disabled on every read tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle
from project_lens.domain.models import Evidence, GraphEvidence, KnowledgeGapReport, ProjectRef
from project_lens.domain.ops import OpsFinding, OpsQuery
from project_lens.graph.query import GraphQuery
from project_lens.runtime.policy import RiskClass
from project_lens.runtime.tool_gateway import ToolAuditEvent
from project_lens.runtime.tool_specs import (
    ToolLane,
    assert_tool_callable,
    list_tool_specs,
)


@dataclass
class ReadContextGateway:
    """Thin ACL + audit wrapper around ContextEngine read tools."""

    engine: ContextEngine
    allow_apply: bool = False
    audit_events: list[ToolAuditEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.allow_apply:
            raise ValueError("ReadContextGateway refuses allow_apply=True")

    def list_read_specs(self):
        return list_tool_specs(category=ToolLane.READ)

    def search_context(
        self,
        query: ContextQuery,
        access: AccessContext,
    ) -> EvidenceBundle:
        assert_tool_callable("search_context", allow_apply=False)
        bundle = self.engine.search(query, access)
        self._audit(
            "search_context",
            {
                "project": _project_ref(query.project),
                "query": query.text[:200],
                "limit": query.limit,
                "hit_count": len(bundle.hits),
            },
            f"hits={len(bundle.hits)}",
            True,
        )
        return bundle

    def authorized_evidence(
        self,
        project: ProjectRef,
        access: AccessContext,
        *,
        limit: int = 50,
    ) -> tuple[Evidence, ...]:
        assert_tool_callable("authorized_evidence", allow_apply=False)
        evidence = self.engine.authorized_evidence(project, access, limit=limit)
        self._audit(
            "authorized_evidence",
            {
                "project": _project_ref(project),
                "limit": limit,
                "hit_count": len(evidence),
            },
            f"hit_count={len(evidence)}",
            True,
        )
        return evidence

    def list_knowledge_gaps(
        self,
        project: ProjectRef,
        access: AccessContext,
    ) -> KnowledgeGapReport:
        assert_tool_callable("list_knowledge_gaps", allow_apply=False)
        report = self.engine.knowledge_gaps(project, access)
        self._audit(
            "list_knowledge_gaps",
            {
                "project": _project_ref(project),
                "gap_count": len(report.gaps),
            },
            f"gap_count={len(report.gaps)}",
            True,
        )
        return report

    def query_graph(
        self,
        project: ProjectRef,
        access: AccessContext,
        query: GraphQuery,
    ) -> tuple[GraphEvidence, ...]:
        assert_tool_callable("query_graph", allow_apply=False)
        paths = self.engine.query_graph(project, access, query)
        self._audit(
            "query_graph",
            {
                "project": _project_ref(project),
                "relation": query.relation,
                "path_count": len(paths),
            },
            f"path_count={len(paths)}",
            True,
        )
        return paths

    def query_ops(self, query: OpsQuery, access: AccessContext) -> OpsFinding:
        """Windowed ops query; audited as query_logs (ephemeral, not long-term RAG)."""

        assert_tool_callable("query_logs", allow_apply=False)
        finding = self.engine.query_ops(query, access)
        self._audit(
            "query_logs",
            {
                "project": _project_ref(query.project),
                "signal_count": len(finding.signals),
                "evidence_count": len(finding.evidence),
                "ephemeral": True,
            },
            f"signals={len(finding.signals)}",
            True,
        )
        return finding

    def audit_summary(self) -> dict[str, Any]:
        names = [event.tool_name for event in self.audit_events]
        return {
            "read_tool_calls": len(self.audit_events),
            "read_tool_names": names,
            "allow_apply": False,
            "ok": all(event.ok for event in self.audit_events),
            "apply_events": [
                event.tool_name
                for event in self.audit_events
                if event.risk_class == RiskClass.APPLY
            ],
        }

    def reset_audit(self) -> None:
        self.audit_events.clear()

    def _audit(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        ok: bool,
    ) -> None:
        self.audit_events.append(
            ToolAuditEvent(
                id=uuid4(),
                tool_name=tool_name,
                risk_class=RiskClass.READ,
                arguments=arguments,
                result=result[:2_000],
                ok=ok,
            )
        )


def _project_ref(project: ProjectRef) -> dict[str, str | None]:
    return {
        "tenant_id": project.tenant_id,
        "project_id": project.project_id,
        "service": project.service,
        "environment": project.environment,
    }
