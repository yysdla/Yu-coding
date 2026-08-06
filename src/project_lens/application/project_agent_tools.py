"""ProjectLens read-only tool envelope for Hermes/runtime integrations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from project_lens.agent.read_tools import (
    InvestigationLedger,
    build_investigation_tool_registry,
)
from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext
from project_lens.domain.models import Evidence, ProjectRef
from project_lens.project_space.policies import (
    ProjectRuntimeContextResolver,
    ResolvedProjectRuntimeContext,
    effective_scope_to_audit_dict,
)
from project_lens.project_space.registry import ProjectRegistry
from project_lens.runtime.policy import EngineeringPolicy
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.runtime.tool_gateway import ToolGateway


_EXTERNAL_TO_INTERNAL: dict[str, str] = {
    "projectlens_search_context": "search_context",
    "projectlens_read_project_file": "read_project_file",
    "projectlens_query_graph": "query_graph",
    "projectlens_authorized_evidence": "authorized_evidence",
    "projectlens_list_knowledge_gaps": "list_knowledge_gaps",
}

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "projectlens_search_context": (
        "Search authorized project evidence for a natural-language query. Use this "
        "when Hermes needs project docs, code snippets, commits, tasks, or incident "
        "evidence before answering. Returns concise hits with citation refs."
    ),
    "projectlens_read_project_file": (
        "Read one allowlisted project file through ProjectLens permissions. Use this "
        "only when a concrete repository path is known. Returns a clipped summary and "
        "citation refs; never use it for secrets or paths outside ProjectSpace allowlists."
    ),
    "projectlens_query_graph": (
        "Query ProjectLens GraphRAG relationships for modules, services, endpoints, "
        "owners, or dependencies. Use when relationships matter more than keyword hits."
    ),
    "projectlens_authorized_evidence": (
        "List ACL-authorized project evidence for orientation. Use when Hermes needs a "
        "bounded inventory of trusted sources in the current ProjectSpace."
    ),
    "projectlens_list_knowledge_gaps": (
        "List known gaps in project documentation, ownership, runbooks, releases, or "
        "coverage. Use when the user asks what ProjectLens still cannot answer well."
    ),
}

_PARAMETERS: dict[str, dict[str, Any]] = {
    "projectlens_search_context": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search text, e.g. create_order coupon"},
            "limit": {"type": "integer", "description": "1-20 results; default 8"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "projectlens_read_project_file": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project-relative allowlisted path, e.g. src/order_service.py",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    "projectlens_query_graph": {
        "type": "object",
        "properties": {
            "relation": {"type": "string"},
            "start_kind": {"type": "string"},
            "start_label": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": [],
        "additionalProperties": False,
    },
    "projectlens_authorized_evidence": {
        "type": "object",
        "properties": {"limit": {"type": "integer"}},
        "required": [],
        "additionalProperties": False,
    },
    "projectlens_list_knowledge_gaps": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
}

_WRITE_OR_UNSAFE_MARKERS = (
    "apply",
    "patch",
    "pr",
    "deploy",
    "rollback",
    "restart",
    "shell",
    "terminal",
    "write",
)


@dataclass(frozen=True)
class ProjectAgentToolCallRequest:
    tool_name: str
    project: ProjectRef
    user_id: str
    chat_id: str
    arguments: dict[str, Any]


class ProjectAgentToolService:
    """ProjectLens Kernel tool facade.

    Hermes may decide *which* ProjectLens tool to call. ProjectLens still decides
    whether the actor/chat/project may read it, executes through gateways, and
    returns only citation-ready observations.
    """

    def __init__(
        self,
        *,
        context_engine: ContextEngine,
        project_registry: ProjectRegistry,
    ) -> None:
        self._engine = context_engine
        self._registry = project_registry
        self._resolver = ProjectRuntimeContextResolver(project_registry=project_registry)

    def list_tools(self) -> dict[str, Any]:
        return {
            "ok": True,
            "tools": [
                {
                    "name": name,
                    "description": _TOOL_DESCRIPTIONS[name],
                    "category": "read",
                    "parameters": _PARAMETERS[name],
                    "allow_apply": False,
                }
                for name in _EXTERNAL_TO_INTERNAL
            ],
        }

    async def call_tool(self, request: ProjectAgentToolCallRequest) -> dict[str, Any]:
        external = request.tool_name.strip()
        internal = _EXTERNAL_TO_INTERNAL.get(external)
        if internal is None:
            code = "TOOL_NOT_ALLOWED" if _looks_write_or_unsafe(external) else "UNKNOWN_TOOL"
            return _error_envelope(
                error_code=code,
                message=f"tool is not exposed by ProjectLens read-only envelope: {external}",
                recovery=(
                    "Retry with one of the projectlens_* read-only tools from "
                    "GET /api/v1/project-agent/tools."
                ),
                request=request,
                tool_name=external,
            )

        try:
            resolved = self._resolver.resolve(
                tenant_id=request.project.tenant_id,
                project_id=request.project.project_id,
                chat_id=request.chat_id,
                user_id=request.user_id,
            )
        except (KeyError, PermissionError) as exc:
            return _error_envelope(
                error_code="ACCESS_DENIED",
                message=str(exc),
                recovery="Ask for access to this ProjectSpace/chat or choose an allowed project.",
                request=request,
                tool_name=external,
                internal_tool_name=internal,
            )

        if internal not in resolved.effective_scope.allowed_tools:
            return _error_envelope(
                error_code="TOOL_NOT_ALLOWED",
                message=f"tool {internal} is not allowed for this actor/chat scope",
                recovery="Use a tool listed in the current visibility scope, or ask in an allowed chat.",
                request=request,
                tool_name=external,
                internal_tool_name=internal,
                resolved=resolved,
            )

        ledger = InvestigationLedger()
        tool_gateway = self._tool_gateway(resolved)
        registry = build_investigation_tool_registry(
            project=resolved.project,
            access=_access_context(resolved),
            read_gateway=ReadContextGateway(self._engine),
            tool_gateway=tool_gateway,
            ledger=ledger,
            access_scope=resolved.project_space.access_scope
            or f"project:{resolved.project.project_id}:read",
        )
        result = await registry.execute(
            tool_call_id=str(uuid4()),
            name=internal,
            arguments=request.arguments,
        )
        if result.is_error:
            return _error_envelope(
                error_code=_tool_error_code(result.content),
                message=result.content,
                recovery=_tool_recovery_hint(internal),
                request=request,
                tool_name=external,
                internal_tool_name=internal,
                resolved=resolved,
            )

        return _success_envelope(
            request=request,
            tool_name=external,
            internal_tool_name=internal,
            result_content=result.content,
            ledger=ledger,
            resolved=resolved,
        )

    def _tool_gateway(self, resolved: ResolvedProjectRuntimeContext) -> ToolGateway | None:
        root = resolved.project_space.primary_repository_root
        if root is None:
            return None
        return ToolGateway(
            EngineeringPolicy(
                project_root=root,
                allowed_path_prefixes=resolved.effective_scope.readable_sources,
                allow_apply=False,
            )
        )


def _access_context(resolved: ResolvedProjectRuntimeContext) -> AccessContext:
    access_scope = resolved.project_space.access_scope or f"project:{resolved.project.project_id}:read"
    return AccessContext(
        tenant_id=resolved.project.tenant_id,
        user_id=resolved.actor_id,
        permissions=frozenset({access_scope}),
    )


def _success_envelope(
    *,
    request: ProjectAgentToolCallRequest,
    tool_name: str,
    internal_tool_name: str,
    result_content: str,
    ledger: InvestigationLedger,
    resolved: ResolvedProjectRuntimeContext,
) -> dict[str, Any]:
    payload = _loads_json(result_content)
    evidence = ledger.all_evidence()
    summary = _summary_for(internal_tool_name, payload, evidence)
    refs = [_evidence_ref(item) for item in evidence[:12]]
    return {
        "ok": True,
        "tool_name": tool_name,
        "internal_tool_name": internal_tool_name,
        "tool_result_id": str(uuid4()),
        "project": _project_payload(resolved.project),
        "summary": summary,
        "citations": refs,
        "evidence_refs": refs,
        "unknowns": _unknowns_for(payload),
        "audit_ref": {
            "tool_name": tool_name,
            "internal_tool_name": internal_tool_name,
            "tenant_id": resolved.project.tenant_id,
            "project_id": resolved.project.project_id,
            "actor_id": request.user_id,
            "chat_id": request.chat_id,
            "allow_apply": False,
            "role": resolved.effective_scope.role.value,
            "read_tool_calls": len(ledger.tool_names),
            "read_tool_names": list(ledger.tool_names),
        },
        "visibility_scope": effective_scope_to_audit_dict(resolved.effective_scope),
    }


def _error_envelope(
    *,
    error_code: str,
    message: str,
    recovery: str,
    request: ProjectAgentToolCallRequest,
    tool_name: str,
    internal_tool_name: str | None = None,
    resolved: ResolvedProjectRuntimeContext | None = None,
) -> dict[str, Any]:
    project = resolved.project if resolved is not None else request.project
    visibility = (
        effective_scope_to_audit_dict(resolved.effective_scope)
        if resolved is not None
        else {
            "tenant_id": request.project.tenant_id,
            "project_id": request.project.project_id,
            "actor_id": request.user_id,
            "chat_id": request.chat_id,
        }
    )
    return {
        "ok": False,
        "tool_name": tool_name,
        "internal_tool_name": internal_tool_name,
        "tool_result_id": str(uuid4()),
        "project": _project_payload(project),
        "summary": None,
        "citations": [],
        "evidence_refs": [],
        "unknowns": [message],
        "error_code": error_code,
        "message": message,
        "retryable": error_code in {"UNKNOWN_TOOL", "TOOL_EXECUTION_FAILED"},
        "agent_recovery_hint": recovery,
        "audit_ref": {
            "tool_name": tool_name,
            "internal_tool_name": internal_tool_name,
            "tenant_id": request.project.tenant_id,
            "project_id": request.project.project_id,
            "actor_id": request.user_id,
            "chat_id": request.chat_id,
            "allow_apply": False,
        },
        "visibility_scope": visibility,
    }


def _project_payload(project: ProjectRef) -> dict[str, str | None]:
    return {
        "tenant_id": project.tenant_id,
        "project_id": project.project_id,
        "service": project.service,
        "environment": project.environment,
    }


def _evidence_ref(item: Evidence) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "kind": item.type.value,
        "source_uri": f"{item.source.system}:{item.source.source_id}",
        "summary": item.content.strip().replace("\n", " ")[:240],
    }


def _summary_for(
    internal_tool_name: str,
    payload: dict[str, Any],
    evidence: tuple[Evidence, ...],
) -> str:
    if internal_tool_name == "search_context":
        hits = payload.get("hits") if isinstance(payload.get("hits"), list) else []
        return f"search_context returned {len(hits)} authorized hit(s)."
    if internal_tool_name == "read_project_file":
        path = payload.get("path") or "requested file"
        return f"read_project_file returned an allowlisted snippet for {path}."
    if internal_tool_name == "query_graph":
        paths = payload.get("paths") if isinstance(payload.get("paths"), list) else []
        return f"query_graph returned {len(paths)} relationship path(s)."
    if internal_tool_name == "authorized_evidence":
        rows = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
        return f"authorized_evidence returned {len(rows)} item(s)."
    if internal_tool_name == "list_knowledge_gaps":
        gaps = payload.get("gaps") if isinstance(payload.get("gaps"), list) else []
        return f"list_knowledge_gaps returned {len(gaps)} gap(s)."
    return f"{internal_tool_name} returned {len(evidence)} citation source(s)."


def _unknowns_for(payload: dict[str, Any]) -> list[str]:
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return [str(item) for item in warnings if str(item).strip()]
    return []


def _loads_json(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {"raw": content[:500]}
    return payload if isinstance(payload, dict) else {"raw": payload}


def _tool_error_code(content: str) -> str:
    if "unknown tool" in content:
        return "UNKNOWN_TOOL"
    if "not allowed" in content or "escapes project root" in content or "Permission" in content:
        return "ACCESS_DENIED"
    return "TOOL_EXECUTION_FAILED"


def _tool_recovery_hint(internal_tool_name: str) -> str:
    if internal_tool_name == "read_project_file":
        return "Retry with a project-relative path under the current ProjectSpace file allowlist."
    return "Retry with valid arguments for this read-only ProjectLens tool."


def _looks_write_or_unsafe(tool_name: str) -> bool:
    lowered = tool_name.casefold()
    return any(marker in lowered for marker in _WRITE_OR_UNSAFE_MARKERS)
