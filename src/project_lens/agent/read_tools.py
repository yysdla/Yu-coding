"""Read-only AgentLoop tools backed by ReadContextGateway / ToolGateway."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID

from project_lens.context.models import AccessContext, ContextQuery
from project_lens.domain.models import (
    Evidence,
    EvidenceType,
    GraphEvidence,
    ProjectRef,
    SourceRef,
)
from project_lens.graph.models import GraphNodeKind
from project_lens.graph.query import GraphQuery
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.runtime.tool_gateway import ToolGateway
from project_lens.runtime.tools import BaseTool, ToolRegistry
from project_lens.runtime.types import ToolCategory

READ_ONLY_INVESTIGATION_TOOLS = frozenset(
    {
        "search_context",
        "authorized_evidence",
        "list_knowledge_gaps",
        "query_graph",
        "read_project_file",
        "list_project_files",
        "grep_project_code",
        "search_project_code",
        "read_project_file_range",
    }
)

WRITE_BLOCKED_TOOL_NAMES = frozenset(
    {
        "apply_patch",
        "create_pr",
        "deploy",
        "rollback",
        "restart",
        "write_file",
        "shell",
        "terminal",
    }
)


@dataclass
class InvestigationLedger:
    """Accumulates Evidence / ToolObservations for citation verification."""

    evidence: dict[UUID, Evidence] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)

    def add_evidence(self, item: Evidence) -> None:
        self.evidence[item.id] = item

    def add_many(self, items: tuple[Evidence, ...] | list[Evidence]) -> None:
        for item in items:
            self.add_evidence(item)

    def record_tool(self, name: str, summary: dict[str, Any]) -> None:
        self.tool_names.append(name)
        self.observations.append({"tool": name, **summary})

    def all_evidence(self) -> tuple[Evidence, ...]:
        return tuple(self.evidence.values())


def build_investigation_tool_registry(
    *,
    project: ProjectRef,
    access: AccessContext,
    read_gateway: ReadContextGateway,
    tool_gateway: ToolGateway | None,
    ledger: InvestigationLedger,
    access_scope: str,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        SearchContextTool(
            project=project,
            access=access,
            gateway=read_gateway,
            ledger=ledger,
        )
    )
    registry.register(
        AuthorizedEvidenceTool(
            project=project,
            access=access,
            gateway=read_gateway,
            ledger=ledger,
        )
    )
    registry.register(
        ListKnowledgeGapsTool(
            project=project,
            access=access,
            gateway=read_gateway,
            ledger=ledger,
        )
    )
    registry.register(
        QueryGraphTool(
            project=project,
            access=access,
            gateway=read_gateway,
            ledger=ledger,
        )
    )
    if tool_gateway is not None:
        file_kwargs = {
            "project": project,
            "gateway": tool_gateway,
            "ledger": ledger,
            "access_scope": access_scope,
        }
        registry.register(ReadProjectFileTool(**file_kwargs))
        registry.register(ListProjectFilesTool(**file_kwargs))
        registry.register(GrepProjectCodeTool(**file_kwargs))
        registry.register(SearchProjectCodeTool(**file_kwargs))
        registry.register(ReadProjectFileRangeTool(**file_kwargs))
    return registry


class SearchContextTool(BaseTool):
    name = "search_context"
    description = "Search project evidence (docs, code, commits) by query text."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        gateway: ReadContextGateway,
        ledger: InvestigationLedger,
    ) -> None:
        self._project = project
        self._access = access
        self._gateway = gateway
        self._ledger = ledger

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        query = str(kwargs["query"])
        limit = int(kwargs.get("limit") or 8)
        bundle = self._gateway.search_context(
            ContextQuery(text=query, project=self._project, limit=min(max(limit, 1), 20)),
            self._access,
        )
        self._ledger.add_many(bundle.evidence)
        self._ledger.record_tool(
            self.name,
            {"query": query[:120], "hit_count": len(bundle.hits)},
        )
        hits = [
            {
                "evidence_id": str(hit.evidence.id),
                "type": hit.evidence.type.value,
                "source_id": hit.evidence.source.source_id,
                "score": hit.score,
                "snippet": hit.evidence.content[:400],
            }
            for hit in bundle.hits[:8]
        ]
        return json.dumps({"hits": hits, "warnings": list(bundle.warnings)}, ensure_ascii=False)


class AuthorizedEvidenceTool(BaseTool):
    name = "authorized_evidence"
    description = "List ACL-authorized evidence for the current project."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        gateway: ReadContextGateway,
        ledger: InvestigationLedger,
    ) -> None:
        self._project = project
        self._access = access
        self._gateway = gateway
        self._ledger = ledger

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "required": [],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        limit = int(kwargs.get("limit") or 20)
        evidence = self._gateway.authorized_evidence(
            self._project, self._access, limit=min(max(limit, 1), 50)
        )
        self._ledger.add_many(evidence)
        self._ledger.record_tool(self.name, {"hit_count": len(evidence)})
        rows = [
            {
                "evidence_id": str(item.id),
                "type": item.type.value,
                "source_id": item.source.source_id,
                "snippet": item.content[:300],
            }
            for item in evidence[:12]
        ]
        return json.dumps({"evidence": rows}, ensure_ascii=False)


class ListKnowledgeGapsTool(BaseTool):
    name = "list_knowledge_gaps"
    description = "List knowledge gaps for the current project."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        gateway: ReadContextGateway,
        ledger: InvestigationLedger,
    ) -> None:
        self._project = project
        self._access = access
        self._gateway = gateway
        self._ledger = ledger

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        report = self._gateway.list_knowledge_gaps(self._project, self._access)
        self._ledger.record_tool(self.name, {"gap_count": len(report.gaps)})
        gaps = [
            {
                "type": gap.type.value,
                "title": gap.title,
                "description": gap.description[:200],
            }
            for gap in report.gaps[:10]
        ]
        return json.dumps({"gaps": gaps}, ensure_ascii=False)


class QueryGraphTool(BaseTool):
    name = "query_graph"
    description = "Query project relationship graph paths."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        access: AccessContext,
        gateway: ReadContextGateway,
        ledger: InvestigationLedger,
    ) -> None:
        self._project = project
        self._access = access
        self._gateway = gateway
        self._ledger = ledger

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "relation": {"type": "string"},
                "start_kind": {"type": "string"},
                "start_label": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        start_kind = kwargs.get("start_kind")
        kind = None
        if start_kind:
            try:
                kind = GraphNodeKind(str(start_kind))
            except ValueError:
                kind = None
        query = GraphQuery(
            start_kind=kind,
            start_label=str(kwargs["start_label"]) if kwargs.get("start_label") else None,
            relation=str(kwargs["relation"]) if kwargs.get("relation") else None,
            limit=min(max(int(kwargs.get("limit") or 5), 1), 20),
        )
        paths: tuple[GraphEvidence, ...] = self._gateway.query_graph(
            self._project, self._access, query
        )
        cited_count = 0
        for path in paths:
            if path.cited_evidence:
                self._ledger.add_many(path.cited_evidence)
                cited_count += len(path.cited_evidence)
            # Fallback: evidence_ids without bodies still need ledger coverage
            # only when cited_evidence already carried the bodies (above).
            # If cited_evidence is empty, evidence_ids alone cannot pass verifier.
        self._ledger.record_tool(
            self.name,
            {"path_count": len(paths), "cited_evidence_count": cited_count},
        )
        rows = [
            {
                "summary": path.summary or " / ".join(path.path_labels),
                "evidence_ids": [str(eid) for eid in path.evidence_ids[:6]],
                "cited_evidence_ids": [str(item.id) for item in path.cited_evidence[:6]],
            }
            for path in paths[:8]
        ]
        return json.dumps({"paths": rows}, ensure_ascii=False)


class ReadProjectFileTool(BaseTool):
    name = "read_project_file"
    description = "Read a project file under the ProjectSpace allowlist (read-only)."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        gateway: ToolGateway,
        ledger: InvestigationLedger,
        access_scope: str,
    ) -> None:
        self._project = project
        self._gateway = gateway
        self._ledger = ledger
        self._access_scope = access_scope

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        relative = str(kwargs["path"]).replace("\\", "/").lstrip("./")
        content = self._gateway.read_project_file(relative)
        clipped = content[:8000]
        digest = sha256(clipped.encode("utf-8")).hexdigest()
        evidence = Evidence(
            type=EvidenceType.CODE,
            project=self._project,
            source=SourceRef(system="repository", source_id=relative),
            content=clipped if clipped.strip() else "(empty file)",
            observed_at=datetime.now(timezone.utc),
            access_scope=self._access_scope,
            content_hash=digest[:32],
            metadata={"path": relative, "tool": "read_project_file"},
        )
        self._ledger.add_evidence(evidence)
        self._ledger.record_tool(self.name, {"path": relative, "chars": len(clipped)})
        return json.dumps(
            {
                "path": relative,
                "evidence_id": str(evidence.id),
                "content": clipped[:2000],
            },
            ensure_ascii=False,
        )


def _code_snippet_evidence(
    *,
    project: ProjectRef,
    access_scope: str,
    source_id: str,
    content: str,
    tool: str,
    metadata: dict[str, Any] | None = None,
) -> Evidence:
    clipped = content[:8000] if content.strip() else "(empty)"
    digest = sha256(clipped.encode("utf-8")).hexdigest()
    meta = {"tool": tool, **(metadata or {})}
    return Evidence(
        type=EvidenceType.CODE,
        project=project,
        source=SourceRef(system="repository", source_id=source_id[:500]),
        content=clipped,
        observed_at=datetime.now(timezone.utc),
        access_scope=access_scope,
        content_hash=digest[:32],
        metadata=meta,
    )


class ListProjectFilesTool(BaseTool):
    name = "list_project_files"
    description = "List files under ProjectSpace file_allowlist (read-only)."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        gateway: ToolGateway,
        ledger: InvestigationLedger,
        access_scope: str,
    ) -> None:
        self._project = project
        self._gateway = gateway
        self._ledger = ledger
        self._access_scope = access_scope

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prefix": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        prefix = str(kwargs.get("prefix") or "src/")
        limit = int(kwargs.get("limit") or 50)
        files = self._gateway.list_project_files(prefix=prefix, limit=limit)
        listing = "\n".join(files) if files else "(no files)"
        evidence = _code_snippet_evidence(
            project=self._project,
            access_scope=self._access_scope,
            source_id=f"list:{prefix}",
            content=listing,
            tool=self.name,
            metadata={"prefix": prefix, "file_count": len(files)},
        )
        self._ledger.add_evidence(evidence)
        self._ledger.record_tool(
            self.name, {"prefix": prefix, "file_count": len(files)}
        )
        return json.dumps(
            {
                "prefix": prefix,
                "files": files,
                "evidence_id": str(evidence.id),
            },
            ensure_ascii=False,
        )


class GrepProjectCodeTool(BaseTool):
    name = "grep_project_code"
    description = "Grep text in allowlisted project files (read-only)."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        gateway: ToolGateway,
        ledger: InvestigationLedger,
        access_scope: str,
    ) -> None:
        self._project = project
        self._gateway = gateway
        self._ledger = ledger
        self._access_scope = access_scope

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "prefix": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        pattern = str(kwargs["pattern"])
        prefix = str(kwargs.get("prefix") or "src/")
        limit = int(kwargs.get("limit") or 20)
        hits = self._gateway.grep_project_code(pattern, prefix=prefix, limit=limit)
        return self._pack_hits(pattern=pattern, prefix=prefix, hits=hits)

    def _pack_hits(
        self, *, pattern: str, prefix: str, hits: list[dict[str, Any]]
    ) -> str:
        body_lines = [
            f"{hit['path']}:{hit['line']}: {hit['text']}" for hit in hits[:20]
        ]
        body = "\n".join(body_lines) if body_lines else f"(no hits for {pattern})"
        evidence = _code_snippet_evidence(
            project=self._project,
            access_scope=self._access_scope,
            source_id=f"grep:{pattern[:80]}",
            content=body,
            tool=self.name,
            metadata={"pattern": pattern[:120], "hit_count": len(hits), "prefix": prefix},
        )
        self._ledger.add_evidence(evidence)
        self._ledger.record_tool(
            self.name, {"pattern": pattern[:120], "hit_count": len(hits)}
        )
        return json.dumps(
            {
                "pattern": pattern,
                "hits": hits,
                "evidence_id": str(evidence.id),
            },
            ensure_ascii=False,
        )


class SearchProjectCodeTool(GrepProjectCodeTool):
    name = "search_project_code"
    description = "Search code under ProjectSpace allowlist (alias of grep_project_code)."

    async def execute(self, **kwargs: Any) -> str:
        pattern = str(kwargs["pattern"])
        prefix = str(kwargs.get("prefix") or "src/")
        limit = int(kwargs.get("limit") or 20)
        hits = self._gateway.search_project_code(pattern, prefix=prefix, limit=limit)
        return self._pack_hits(pattern=pattern, prefix=prefix, hits=hits)


class ReadProjectFileRangeTool(BaseTool):
    name = "read_project_file_range"
    description = "Read a line range from an allowlisted project file (read-only)."
    category = ToolCategory.READ
    required_permission = "project:read"

    def __init__(
        self,
        *,
        project: ProjectRef,
        gateway: ToolGateway,
        ledger: InvestigationLedger,
        access_scope: str,
    ) -> None:
        self._project = project
        self._gateway = gateway
        self._ledger = ledger
        self._access_scope = access_scope

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        relative = str(kwargs["path"]).replace("\\", "/").lstrip("./")
        start = int(kwargs.get("start_line") or 1)
        end = int(kwargs.get("end_line") or start + 79)
        payload = self._gateway.read_project_file_range(
            relative, start_line=start, end_line=end
        )
        content = str(payload.get("content") or "")
        evidence = _code_snippet_evidence(
            project=self._project,
            access_scope=self._access_scope,
            source_id=f"{relative}:{payload.get('start_line')}-{payload.get('end_line')}",
            content=content,
            tool=self.name,
            metadata={
                "path": relative,
                "start_line": payload.get("start_line"),
                "end_line": payload.get("end_line"),
            },
        )
        self._ledger.add_evidence(evidence)
        self._ledger.record_tool(
            self.name,
            {
                "path": relative,
                "start_line": payload.get("start_line"),
                "end_line": payload.get("end_line"),
            },
        )
        return json.dumps(
            {
                **payload,
                "evidence_id": str(evidence.id),
                "content": content[:2000],
            },
            ensure_ascii=False,
        )
