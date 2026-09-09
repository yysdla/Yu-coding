"""ProjectLens read-only tool envelope for Hermes/runtime integrations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from project_lens.agent.read_tools import (
    InvestigationLedger,
    build_investigation_tool_registry,
)
from project_lens.context.engine import ContextEngine
from project_lens.context.memory_store import MemoryStore
from project_lens.context.memory_retrieval import retrieve_memories
from project_lens.context.history_memory import derive_episode
from project_lens.context.history_store import HistoryMemoryStore
from project_lens.context.models import AccessContext
from project_lens.domain.access import AccessDeniedAnswer, access_denied_for_permission_error
from project_lens.domain.identity import ChatType
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
from project_lens.application.hermes_proposals import (
    propose_patch_plan,
    propose_project_todo,
    propose_risk_escalation,
    propose_test_plan,
)
from project_lens.application.hermes_validation import (
    check_diff_scope,
    validate_patch_plan,
    verify_evidence_links,
)
from project_lens.runtime.patch_plan import FilePatch, PatchPlan


_EXTERNAL_TO_INTERNAL: dict[str, str] = {
    "projectlens_search_context": "search_context",
    "projectlens_read_project_file": "read_project_file",
    "projectlens_list_project_files": "list_project_files",
    "projectlens_query_graph": "query_graph",
    "projectlens_authorized_evidence": "authorized_evidence",
    "projectlens_list_knowledge_gaps": "list_knowledge_gaps",
}

_MEMORY_EXTERNAL_TO_INTERNAL: dict[str, str] = {
    "projectlens_search_project_memory": "search_project_memory",
    "projectlens_get_memory_detail": "get_memory_detail",
}

_HISTORY_EXTERNAL_TO_INTERNAL: dict[str, str] = {
    "projectlens_search_project_history": "search_project_history",
    "projectlens_get_run_detail": "get_run_detail",
}

_ADVANCED_EXTERNAL_TO_INTERNAL: dict[str, str] = {
    "projectlens_propose_patch_plan": "propose_patch_plan",
    "projectlens_propose_test_plan": "propose_test_plan",
    "projectlens_propose_risk_escalation": "propose_risk_escalation",
    "projectlens_propose_project_todo": "propose_project_todo",
    "projectlens_validate_patch_plan": "validate_patch_plan",
    "projectlens_check_diff_scope": "check_diff_scope",
    "projectlens_verify_evidence_links": "verify_evidence_links",
}

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "projectlens_search_project_memory": (
        "Search approved, active long-term project memories and return a small set of "
        "citation-ready memory cards. ProjectLens enforces project and chat scope, "
        "approval, validity, deduplication, and a hard response budget."
    ),
    "projectlens_get_memory_detail": (
        "Read one approved, active project memory by exact memory_id. ProjectLens"
        " re-checks project/chat scope and validity before returning bounded detail."
    ),
    "projectlens_search_project_history": (
        "Search bounded historical AgentRuns for the current project. Returns "
        "safe summaries, status, tool names, and Evidence ids; raw tool arguments "
        "and sensitive bodies are excluded."
    ),
    "projectlens_get_run_detail": (
        "Read one historical AgentRun by exact run_id after re-checking current "
        "project scope. Returns bounded question, answer summaries, citations, "
        "and event names without raw tool arguments."
    ),
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
    "projectlens_list_project_files": (
        "List allowlisted project files through ProjectLens permissions. Use this for "
        "project introduction or identity questions when the README path is not known; "
        "then read README.md or a public docs file for citation-ready content."
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
    "projectlens_search_project_memory": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Memory search text"},
            "memory_types": {"type": "array", "items": {"type": "string"}, "description": "Optional memory type filter"},
            "service": {"type": "string", "description": "Optional project service filter"},
            "limit": {"type": "integer", "description": "1-8 cards; server capped"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "projectlens_get_memory_detail": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Exact memory id returned by search_project_memory"},
        },
        "required": ["memory_id"],
        "additionalProperties": False,
    },
    "projectlens_search_project_history": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Historical run search text"},
            "from": {"type": "string", "description": "Optional ISO date lower bound"},
            "to": {"type": "string", "description": "Optional ISO date upper bound"},
            "limit": {"type": "integer", "description": "1-8 historical summaries; server capped"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "projectlens_get_run_detail": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Exact run id returned by history search"},
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
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
    "projectlens_list_project_files": {
        "type": "object",
        "properties": {
            "prefix": {"type": "string", "description": "Allowlisted path prefix, e.g. docs/"},
            "limit": {"type": "integer", "description": "Maximum file names"},
        },
        "required": [],
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
_ADVANCED_PARAMETERS: dict[str, dict[str, Any]] = {
    "projectlens_propose_patch_plan": {
        "type": "object", "properties": {
            "title": {"type": "string"}, "rationale": {"type": "string"},
            "patches": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}
            }, "required": ["path", "old_text", "new_text"], "additionalProperties": False}},
            "test_commands": {"type": "array", "items": {"type": "string"}},
        }, "required": ["title", "rationale"], "additionalProperties": False,
    },
    "projectlens_propose_test_plan": {
        "type": "object", "properties": {"title": {"type": "string"}, "commands": {"type": "array", "items": {"type": "string"}}},
        "required": ["title", "commands"], "additionalProperties": False,
    },
    "projectlens_propose_risk_escalation": {
        "type": "object", "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
        "required": ["title", "description"], "additionalProperties": False,
    },
    "projectlens_propose_project_todo": {
        "type": "object", "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "owner_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["title", "description"], "additionalProperties": False,
    },
    "projectlens_validate_patch_plan": {
        "type": "object", "properties": {
            "title": {"type": "string"}, "rationale": {"type": "string"},
            "patches": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}
            }, "required": ["path", "old_text", "new_text"], "additionalProperties": False}},
            "test_commands": {"type": "array", "items": {"type": "string"}},
        }, "required": ["title", "rationale", "patches"], "additionalProperties": False,
    },
    "projectlens_check_diff_scope": {
        "type": "object", "properties": {
            "title": {"type": "string"}, "rationale": {"type": "string"},
            "patches": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}
            }, "required": ["path", "old_text", "new_text"], "additionalProperties": False}},
        }, "required": ["title", "rationale", "patches"], "additionalProperties": False,
    },
    "projectlens_verify_evidence_links": {
        "type": "object", "properties": {
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "citation_ids": {"type": "array", "items": {"type": "string"}},
        }, "required": ["evidence_ids", "citation_ids"], "additionalProperties": False,
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
    chat_type: ChatType = "group"
    identity_source: str = "member_directory"


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
        memory_store: MemoryStore | None = None,
        run_repository: Any | None = None,
        event_sink: Any | None = None,
        history_store: HistoryMemoryStore | None = None,
        memory_vector_scorer: Any | None = None,
        advanced_tools_enabled: bool = False,
    ) -> None:
        self._engine = context_engine
        self._registry = project_registry
        self._resolver = ProjectRuntimeContextResolver(project_registry=project_registry)
        self._memory_store = memory_store
        self._run_repository = run_repository
        self._event_sink = event_sink
        self._history_store = history_store
        self._memory_vector_scorer = memory_vector_scorer
        self._advanced_tools_enabled = advanced_tools_enabled

    def list_tools(self) -> dict[str, Any]:
        tools = [
            {
                "name": name,
                "description": _TOOL_DESCRIPTIONS[name],
                "category": "read",
                "parameters": _PARAMETERS[name],
                "allow_apply": False,
            }
            for name in _EXTERNAL_TO_INTERNAL
        ]
        if self._memory_store is not None:
            tools.insert(0, {
                "name": "projectlens_search_project_memory",
                "description": _TOOL_DESCRIPTIONS["projectlens_search_project_memory"],
                "category": "read",
                "parameters": _PARAMETERS["projectlens_search_project_memory"],
                "allow_apply": False,
            })
        if self._run_repository is not None:
            tools.insert(2, {
                "name": "projectlens_search_project_history",
                "description": _TOOL_DESCRIPTIONS["projectlens_search_project_history"],
                "category": "read",
                "parameters": _PARAMETERS["projectlens_search_project_history"],
                "allow_apply": False,
            })
            tools.insert(3, {
                "name": "projectlens_get_run_detail",
                "description": _TOOL_DESCRIPTIONS["projectlens_get_run_detail"],
                "category": "read",
                "parameters": _PARAMETERS["projectlens_get_run_detail"],
                "allow_apply": False,
            })
            tools.insert(1, {
                "name": "projectlens_get_memory_detail",
                "description": _TOOL_DESCRIPTIONS["projectlens_get_memory_detail"],
                "category": "read",
                "parameters": _PARAMETERS["projectlens_get_memory_detail"],
                "allow_apply": False,
            })
        if self._advanced_tools_enabled:
            tools.extend({
                "name": name,
                "description": f"Approval-gated Hermes draft/report tool: {name}.",
                "category": "propose" if "validate" not in name else "validate",
                "parameters": _ADVANCED_PARAMETERS[name],
                "allow_apply": False,
                "requires_approval": "validate" not in name,
            } for name in _ADVANCED_EXTERNAL_TO_INTERNAL)
        return {
            "ok": True,
            "tools": tools,
        }

    async def call_tool(self, request: ProjectAgentToolCallRequest) -> dict[str, Any]:
        external = request.tool_name.strip()
        internal = _EXTERNAL_TO_INTERNAL.get(external)
        is_memory_search = external in _MEMORY_EXTERNAL_TO_INTERNAL
        is_history_tool = external in _HISTORY_EXTERNAL_TO_INTERNAL
        if internal is None and is_memory_search and self._memory_store is not None:
            internal = _MEMORY_EXTERNAL_TO_INTERNAL[external]
        if internal is None and is_history_tool and self._run_repository is not None:
            internal = _HISTORY_EXTERNAL_TO_INTERNAL[external]
        if internal is None and self._advanced_tools_enabled:
            internal = _ADVANCED_EXTERNAL_TO_INTERNAL.get(external)
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
                chat_type=request.chat_type,
                identity_source=request.identity_source,
            )
        except (KeyError, PermissionError) as exc:
            denied = access_denied_for_permission_error(str(exc))
            return _error_envelope(
                error_code="ACCESS_DENIED",
                message=denied.safe_message,
                recovery=denied.next_step,
                request=request,
                tool_name=external,
                internal_tool_name=internal,
                access_denied=denied,
            )

        memory_tool_inherits_context_scope = (
            internal in {"search_project_memory", "get_memory_detail"}
            and "search_context" in resolved.effective_scope.allowed_tools
        )
        history_tool_inherits_context_scope = (
            internal in {"search_project_history", "get_run_detail"}
            and "search_context" in resolved.effective_scope.allowed_tools
        )
        if internal not in resolved.effective_scope.allowed_tools and not (
            memory_tool_inherits_context_scope or history_tool_inherits_context_scope
        ):
            denied = AccessDeniedAnswer(
                reason_code="tool_not_allowed",
                safe_message="当前身份或聊天环境不允许使用该读取工具。",
                next_step="请改用允许的只读工具，或在私聊中提问以获取更完整资料。",
            )
            return _error_envelope(
                error_code="TOOL_NOT_ALLOWED",
                message=denied.safe_message,
                recovery=denied.next_step,
                request=request,
                tool_name=external,
                internal_tool_name=internal,
                resolved=resolved,
                access_denied=denied,
            )

        if internal in _ADVANCED_EXTERNAL_TO_INTERNAL.values():
            return self._call_advanced(
                request=request, external=external, internal=internal, resolved=resolved,
            )

        if internal == "search_project_memory":
            return self._search_project_memory(request=request, external=external, resolved=resolved)

        if internal == "get_memory_detail":
            return self._get_memory_detail(request=request, external=external, resolved=resolved)

        if internal == "search_project_history":
            return self._search_project_history(request=request, external=external, resolved=resolved)

        if internal == "get_run_detail":
            return self._get_run_detail(request=request, external=external, resolved=resolved)

        ledger = InvestigationLedger()
        tool_gateway = self._tool_gateway(resolved)
        read_gateway = ReadContextGateway(self._engine, require_scope=True)
        registry = build_investigation_tool_registry(
            project=resolved.project,
            access=_access_context(resolved),
            read_gateway=read_gateway,
            tool_gateway=tool_gateway,
            ledger=ledger,
            access_scope=resolved.project_space.access_scope
            or f"project:{resolved.project.project_id}:read",
            effective_scope=resolved.effective_scope,
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

    def _search_project_memory(
        self,
        *,
        request: ProjectAgentToolCallRequest,
        external: str,
        resolved: ResolvedProjectRuntimeContext,
    ) -> dict[str, Any]:
        if self._memory_store is None:
            return _error_envelope(
                error_code="TOOL_NOT_CONFIGURED",
                message="project memory search is not configured",
                recovery="Use projectlens_search_context for current evidence.",
                request=request,
                tool_name=external,
                internal_tool_name="search_project_memory",
                resolved=resolved,
            )
        candidates = self._memory_store.search_memories(
            resolved.project,
            str(request.arguments.get("query") or ""),
            limit=128,
        )
        requested_types = {
            str(item).strip().casefold()
            for item in request.arguments.get("memory_types", [])
            if str(item).strip()
        }
        requested_service = str(request.arguments.get("service") or "").strip()
        if requested_types:
            candidates = tuple(
                item for item in candidates
                if item.memory_type.value.casefold() in requested_types
            )
        if requested_service:
            candidates = tuple(
                item for item in candidates
                if item.project.service == requested_service
            )
        result = retrieve_memories(
            str(request.arguments.get("query") or ""),
            candidates,
            limit=int(request.arguments.get("limit", 5)),
            vector_scorer=self._memory_vector_scorer,
        )
        payload = {
            "query": result.query,
            "retrieval_mode": result.retrieval_mode,
            "candidate_count": result.candidate_count,
            "returned_count": result.returned_count,
            "omitted_count": result.omitted_count,
            "budget_chars": result.budget_chars,
            "memories": [
                {
                    "memory_id": card.memory_id,
                    "type": card.memory_type,
                    "title": card.title,
                    "snippet": card.snippet,
                    "evidence_ids": list(card.evidence_ids),
                    "valid_from": card.valid_from,
                    "relevance_score": card.relevance_score,
                    "retrieval_channels": list(card.retrieval_channels),
                    "detail_available": card.detail_available,
                }
                for card in result.cards
            ],
            "warnings": list(result.warnings),
            "conflicts": list(result.conflicts),
        }
        envelope = _success_envelope(
            request=request,
            tool_name=external,
            internal_tool_name="search_project_memory",
            result_content=json.dumps(payload),
            ledger=InvestigationLedger(),
            resolved=resolved,
        )
        envelope["result"] = payload
        envelope["audit_ref"].update(
            {
                "memory_retrieval": True,
                "candidate_count": result.candidate_count,
                "returned_count": result.returned_count,
                "omitted_count": result.omitted_count,
                "retrieval_mode": result.retrieval_mode,
                "budget_chars": result.budget_chars,
                "conflict_count": len(result.conflicts),
            }
        )
        return envelope

    def _get_memory_detail(
        self,
        *,
        request: ProjectAgentToolCallRequest,
        external: str,
        resolved: ResolvedProjectRuntimeContext,
    ) -> dict[str, Any]:
        if self._memory_store is None:
            return _error_envelope(
                error_code="TOOL_NOT_CONFIGURED",
                message="project memory detail is not configured",
                recovery="Use projectlens_search_context for current evidence.",
                request=request,
                tool_name=external,
                internal_tool_name="get_memory_detail",
                resolved=resolved,
            )
        raw_id = request.arguments.get("memory_id")
        try:
            memory_id = UUID(str(raw_id))
        except (TypeError, ValueError, AttributeError):
            return _error_envelope(
                error_code="INVALID_ARGUMENTS",
                message="memory_id must be a valid UUID returned by project memory search",
                recovery="Call projectlens_search_project_memory first and pass one returned memory_id.",
                request=request,
                tool_name=external,
                internal_tool_name="get_memory_detail",
                resolved=resolved,
            )
        memory = self._memory_store.get_memory(memory_id)
        if (
            memory is None
            or memory.project.tenant_id != resolved.project.tenant_id
            or memory.project.project_id != resolved.project.project_id
        ):
            return _error_envelope(
                error_code="MEMORY_NOT_FOUND",
                message="该项目中不存在可读取的长期记忆。",
                recovery="Retry with a memory_id returned for the current project.",
                request=request,
                tool_name=external,
                internal_tool_name="get_memory_detail",
                resolved=resolved,
            )
        from datetime import datetime, timezone

        if memory.valid_to is not None and memory.valid_to <= datetime.now(timezone.utc):
            return _error_envelope(
                error_code="MEMORY_NOT_FOUND",
                message="该长期记忆已失效，不能继续读取。",
                recovery="Search current project memory again for an active replacement.",
                request=request,
                tool_name=external,
                internal_tool_name="get_memory_detail",
                resolved=resolved,
            )
        payload = {
            "memory_id": str(memory.id),
            "type": memory.memory_type.value,
            "text": memory.text,
            "approved_by": memory.approved_by,
            "valid_from": memory.valid_from.isoformat(),
            "valid_to": memory.valid_to.isoformat() if memory.valid_to else None,
            "proposal_id": str(memory.proposal_id) if memory.proposal_id else None,
            "evidence_ids": [str(item) for item in memory.evidence_ids],
            "detail_available": True,
        }
        envelope = _success_envelope(
            request=request,
            tool_name=external,
            internal_tool_name="get_memory_detail",
            result_content=json.dumps(payload, ensure_ascii=False),
            ledger=InvestigationLedger(),
            resolved=resolved,
        )
        envelope["result"] = payload
        envelope["audit_ref"].update({
            "memory_retrieval": True,
            "memory_id": str(memory.id),
            "retrieval_mode": "exact",
            "detail_read": True,
        })
        return envelope

    def _search_project_history(
        self,
        *,
        request: ProjectAgentToolCallRequest,
        external: str,
        resolved: ResolvedProjectRuntimeContext,
    ) -> dict[str, Any]:
        if self._run_repository is None:
            return _error_envelope(
                error_code="TOOL_NOT_CONFIGURED",
                message="project history search is not configured",
                recovery="Use projectlens_search_context or projectlens_search_project_memory.",
                request=request,
                tool_name=external,
                internal_tool_name="search_project_history",
                resolved=resolved,
            )
        query = str(request.arguments.get("query") or "").strip()
        limit = min(max(1, int(request.arguments.get("limit", 5))), 8)
        raw_from = request.arguments.get("from")
        raw_to = request.arguments.get("to")
        lower_bound = _parse_history_date(raw_from, end=False)
        upper_bound = _parse_history_date(raw_to, end=True)
        if (isinstance(raw_from, str) and raw_from.strip() and lower_bound is None) or (
            isinstance(raw_to, str) and raw_to.strip() and upper_bound is None
        ):
            return _error_envelope(
                error_code="INVALID_ARGUMENTS",
                message="from/to must be valid ISO dates or datetimes",
                recovery="Retry with YYYY-MM-DD or an ISO-8601 timestamp.",
                request=request,
                tool_name=external,
                internal_tool_name="search_project_history",
                resolved=resolved,
            )
        if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
            return _error_envelope(
                error_code="INVALID_ARGUMENTS",
                message="from must not be later than to",
                recovery="Swap the date bounds and retry the history search.",
                request=request,
                tool_name=external,
                internal_tool_name="search_project_history",
                resolved=resolved,
            )
        query_tokens = _history_tokens(query)
        history_vector_enabled = bool(
            self._history_store is not None
            and getattr(self._history_store, "uses_vector_scoring", False)
        )
        if self._history_store is not None and hasattr(self._history_store, "search_episodes"):
            episodes = self._history_store.search_episodes(resolved.project, query, limit=128)
            if not episodes and query:
                # FTS tokenizers are weak for some CJK queries; broaden once and
                # let the bounded application-level matcher decide relevance.
                episodes = self._history_store.search_episodes(resolved.project, "", limit=128)
            candidate_runs = tuple(
                run for run in (self._run_repository.get(item.run_id) for item in episodes)
                if run is not None
            )
        else:
            candidate_runs = self._run_repository.list_recent(resolved.project, limit=128)
        scored: list[tuple[float, Any]] = []
        for candidate_index, run in enumerate(candidate_runs):
            if lower_bound is not None and run.updated_at < lower_bound:
                continue
            if upper_bound is not None and run.created_at > upper_bound:
                continue
            run_events = self._event_sink.for_run(run.id) if self._event_sink is not None and hasattr(self._event_sink, "for_run") else ()
            episode, observations = derive_episode(run, run_events)
            haystack = " ".join(
                (
                    run.question,
                    episode.summary,
                    " ".join(episode.tool_names),
                    " ".join(item.text for item in observations),
                    run.error or "",
                )
            ).casefold()
            matched = sum(1 for token in query_tokens if token in haystack)
            if query_tokens and matched == 0 and not history_vector_enabled:
                continue
            score = matched / max(1, len(query_tokens)) if query_tokens else 0.1
            if history_vector_enabled and matched == 0:
                # The history store has already applied semantic scoring on the
                # authorized project partition; preserve those candidates even
                # when the wording has no exact token overlap.
                score = 0.01 + (1.0 / max(1, candidate_index + 1)) * 0.001
            scored.append((score, run))
        scored.sort(key=lambda item: (-item[0], -item[1].updated_at.timestamp()))
        rows = [_history_card(run, self._event_sink, self._history_store) for _, run in scored[:limit]]
        payload = {
            "query": query,
            "returned_count": len(rows),
            "omitted_count": max(0, len(scored) - len(rows)),
            "history": rows,
            "warnings": ["no historical AgentRun matched the query"] if not scored else [],
        }
        envelope = _success_envelope(
            request=request,
            tool_name=external,
            internal_tool_name="search_project_history",
            result_content=json.dumps(payload, ensure_ascii=False),
            ledger=InvestigationLedger(),
            resolved=resolved,
        )
        envelope["result"] = payload
        envelope["audit_ref"].update({
            "history_retrieval": True,
            "returned_count": len(rows),
            "omitted_count": max(0, len(scored) - len(rows)),
            "retrieval_mode": "hybrid" if history_vector_enabled else "bm25",
        })
        return envelope

    def _get_run_detail(
        self,
        *,
        request: ProjectAgentToolCallRequest,
        external: str,
        resolved: ResolvedProjectRuntimeContext,
    ) -> dict[str, Any]:
        if self._run_repository is None:
            return _error_envelope(
                error_code="TOOL_NOT_CONFIGURED",
                message="project run detail is not configured",
                recovery="Use projectlens_search_context for current evidence.",
                request=request,
                tool_name=external,
                internal_tool_name="get_run_detail",
                resolved=resolved,
            )
        try:
            run_id = UUID(str(request.arguments.get("run_id")))
        except (TypeError, ValueError, AttributeError):
            return _error_envelope(
                error_code="INVALID_ARGUMENTS",
                message="run_id must be a valid UUID returned by project history search",
                recovery="Call projectlens_search_project_history first.",
                request=request,
                tool_name=external,
                internal_tool_name="get_run_detail",
                resolved=resolved,
            )
        run = self._run_repository.get(run_id)
        if run is None or run.project.tenant_id != resolved.project.tenant_id or run.project.project_id != resolved.project.project_id:
            return _error_envelope(
                error_code="RUN_NOT_FOUND",
                message="当前项目中不存在可读取的历史运行。",
                recovery="Retry with a run_id returned for the current project.",
                request=request,
                tool_name=external,
                internal_tool_name="get_run_detail",
                resolved=resolved,
            )
        events = self._event_sink.for_run(run.id) if self._event_sink is not None and hasattr(self._event_sink, "for_run") else ()
        payload = _history_detail(run, events, self._history_store)
        envelope = _success_envelope(
            request=request,
            tool_name=external,
            internal_tool_name="get_run_detail",
            result_content=json.dumps(payload, ensure_ascii=False),
            ledger=InvestigationLedger(),
            resolved=resolved,
        )
        envelope["result"] = payload
        envelope["audit_ref"].update({
            "history_retrieval": True,
            "run_id": str(run.id),
            "retrieval_mode": "exact",
            "detail_read": True,
        })
        return envelope
    def _call_advanced(self, *, request: ProjectAgentToolCallRequest, external: str,
                       internal: str, resolved: ResolvedProjectRuntimeContext) -> dict[str, Any]:
        args = request.arguments
        try:
            if internal == "propose_patch_plan":
                patches = tuple(FilePatch(str(item["path"]), str(item.get("old_text", "")), str(item.get("new_text", ""))) for item in args.get("patches", []))
                proposal, plan = propose_patch_plan(project=resolved.project, title=str(args.get("title", "")), rationale=str(args.get("rationale", "")), patches=patches, test_commands=tuple(str(x) for x in args.get("test_commands", [])))
                payload = {"proposal_id": str(proposal.id), "kind": proposal.kind, "requires_approval": proposal.requires_approval, "plan": {"title": plan.title, "rationale": plan.rationale, "affected_paths": list(plan.affected_paths()), "test_commands": list(plan.test_commands)}}
            elif internal == "propose_test_plan":
                proposal = propose_test_plan(project=resolved.project, title=str(args.get("title", "")), commands=tuple(str(x) for x in args.get("commands", [])))
                payload = {"proposal_id": str(proposal.id), "kind": proposal.kind, "requires_approval": proposal.requires_approval, "description": proposal.description}
            elif internal == "propose_risk_escalation":
                proposal = propose_risk_escalation(project=resolved.project, title=str(args.get("title", "")), description=str(args.get("description", "")))
                payload = {"proposal_id": str(proposal.id), "kind": proposal.kind, "requires_approval": proposal.requires_approval, "description": proposal.description}
            elif internal == "propose_project_todo":
                proposal = propose_project_todo(project=resolved.project, title=str(args.get("title", "")), description=str(args.get("description", "")), owner_ids=tuple(str(x) for x in args.get("owner_ids", [])))
                payload = {"proposal_id": str(proposal.id), "kind": proposal.kind, "requires_approval": proposal.requires_approval, "description": proposal.description}
            elif internal == "verify_evidence_links":
                result = verify_evidence_links(evidence_ids=tuple(str(x) for x in args.get("evidence_ids", [])), citation_ids=tuple(str(x) for x in args.get("citation_ids", [])))
                payload = {"ok": result.ok, "errors": list(result.errors), "warnings": list(result.warnings), "execution": "not_performed"}
            elif internal == "check_diff_scope":
                patches = tuple(FilePatch(str(item["path"]), str(item.get("old_text", "")), str(item.get("new_text", ""))) for item in args.get("patches", []))
                result = check_diff_scope(PatchPlan(title=str(args.get("title", "")), rationale=str(args.get("rationale", "")), patches=patches))
                payload = {"ok": result.ok, "errors": list(result.errors), "warnings": list(result.warnings), "affected_paths": list(result.affected_paths), "execution": "not_performed"}
            else:
                patches = tuple(FilePatch(str(item["path"]), str(item.get("old_text", "")), str(item.get("new_text", ""))) for item in args.get("patches", []))
                result = validate_patch_plan(PatchPlan(title=str(args.get("title", "")), rationale=str(args.get("rationale", "")), patches=patches, test_commands=tuple(str(x) for x in args.get("test_commands", []))))
                payload = {"ok": result.ok, "errors": list(result.errors), "warnings": list(result.warnings), "affected_paths": list(result.affected_paths), "test_commands": list(result.test_commands), "execution": "not_performed"}
        except (KeyError, TypeError, ValueError) as exc:
            return _error_envelope(error_code="INVALID_ARGUMENTS", message=str(exc), recovery="Provide the structured draft arguments required by the tool.", request=request, tool_name=external, internal_tool_name=internal, resolved=resolved)
        envelope = _success_envelope(
            request=request,
            tool_name=external,
            internal_tool_name=internal,
            result_content=json.dumps(payload),
            ledger=InvestigationLedger(),
            resolved=resolved,
        )
        envelope["result"] = payload
        envelope["audit_ref"]["advanced_tool"] = True
        envelope["audit_ref"]["requires_approval"] = internal != "validate_patch_plan"
        return envelope

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
            "identity_source": resolved.effective_scope.identity_source,
            "policy_version": resolved.effective_scope.policy_version,
            "chat_type": resolved.effective_scope.chat_type,
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
    access_denied: AccessDeniedAnswer | None = None,
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
        **(
            {"access_denied": access_denied.to_dict()}
            if access_denied is not None
            else {}
        ),
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
        "revision": item.metadata.get("revision") or item.metadata.get("version"),
        "status": item.metadata.get("status"),
        "observed_at": item.observed_at.isoformat(),
        "evidence_ref": str(item.id),
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
    if internal_tool_name == "search_project_memory":
        cards = payload.get("memories") if isinstance(payload.get("memories"), list) else []
        return f"search_project_memory returned {len(cards)} bounded memory card(s)."
    if internal_tool_name == "get_memory_detail":
        return "get_memory_detail returned one authorized project memory detail."
    if internal_tool_name == "search_project_history":
        rows = payload.get("history") if isinstance(payload.get("history"), list) else []
        return f"search_project_history returned {len(rows)} bounded historical episode(s)."
    if internal_tool_name == "get_run_detail":
        return "get_run_detail returned one authorized historical episode detail."
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


def _history_tokens(text: str) -> tuple[str, ...]:
    import re

    return tuple(re.findall(r"[a-z0-9_./:-]+|[\u4e00-\u9fff]", text.casefold()))


def _parse_history_date(value: object, *, end: bool) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if end and len(value.strip()) <= 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _history_card(run: Any, event_sink: Any | None, history_store: Any | None = None) -> dict[str, Any]:
    events = event_sink.for_run(run.id) if event_sink is not None and hasattr(event_sink, "for_run") else ()
    episode = history_store.get_episode(run.id) if history_store is not None else None
    observations = history_store.list_observations(run.id) if history_store is not None else ()
    if episode is None:
        episode, observations = derive_episode(run, events)
    answer = run.answer
    summary = (
        (answer.conclusion if answer else "")
        or (answer.technical_summary if answer else "")
        or (answer.business_summary if answer else "")
        or run.error
        or "未生成回答"
    )
    return {
        "run_id": str(run.id),
        "episode_id": str(episode.id),
        "record_kind": "episode",
        "observed_at": run.updated_at.isoformat(),
        "title": run.question[:120],
        "summary": summary[:420].replace("\n", " "),
        "tool_names": list(episode.tool_names[:12]),
        "evidence_ids": [str(item) for item in episode.evidence_ids[:12]],
        "observation_count": len(observations),
        "status": run.status.value,
        "detail_available": True,
    }


def _history_detail(run: Any, events: Any, history_store: Any | None = None) -> dict[str, Any]:
    episode = history_store.get_episode(run.id) if history_store is not None else None
    observations = history_store.list_observations(run.id) if history_store is not None else ()
    if episode is None:
        episode, observations = derive_episode(run, events)
    answer = run.answer
    return {
        "run_id": str(run.id),
        "trace_id": str(run.trace_id),
        "episode": {
            "episode_id": str(episode.id),
            "title": episode.title,
            "summary": episode.summary[:1_000],
            "started_at": episode.started_at.isoformat(),
            "ended_at": episode.ended_at.isoformat(),
            "status": episode.status,
            "tool_names": list(episode.tool_names),
            "evidence_ids": [str(item) for item in episode.evidence_ids[:24]],
        },
        "question": run.question[:2_000],
        "status": run.status.value,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "answer": {
            "conclusion": answer.conclusion[:1_000] if answer else None,
            "business_summary": answer.business_summary[:1_000] if answer else None,
            "technical_summary": answer.technical_summary[:1_000] if answer else None,
            "unknowns": list(answer.unknowns[:12]) if answer else [],
            "evidence_ids": [str(item.id) for item in (answer.evidence[:12] if answer else ())],
        },
        "observations": [
            {
                "observation_id": str(item.id),
                "kind": item.kind.value,
                "observed_at": item.observed_at.isoformat(),
                "text": item.text,
                "tool_name": item.tool_name,
                "event_type": item.event_type,
                "evidence_ids": [str(evidence_id) for evidence_id in item.evidence_ids],
            }
            for item in observations
        ],
        "events": [
            {
                "type": event.type.value,
                "occurred_at": event.occurred_at.isoformat(),
                "tool": event.payload.get("tool") if isinstance(event.payload, dict) else None,
                "is_error": event.payload.get("is_error") if isinstance(event.payload, dict) else None,
            }
            for event in tuple(events)[-24:]
        ],
        "raw_tool_arguments_available": False,
    }
