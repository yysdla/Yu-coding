"""Agent tool adapter for the project context engine."""

from __future__ import annotations

from typing import Any

from project_lens.context.engine import ContextEngine
from project_lens.context.models import AccessContext, ContextQuery
from project_lens.domain.models import EvidenceType
from project_lens.runtime.security import RunContext
from project_lens.runtime.tools import BaseTool
from project_lens.runtime.types import ToolCategory


class SearchProjectContextTool(BaseTool):
    name = "search_project_context"
    description = "Search authorized project code, documents, and incident evidence"
    category = ToolCategory.READ
    required_permission = "context:search"

    def __init__(self, engine: ContextEngine, run_context: RunContext) -> None:
        self._engine = engine
        self._run_context = run_context

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project_id": {"type": "string"},
                "service": {"type": "string"},
                "source_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": [item.value for item in EvidenceType]},
                },
                "limit": {"type": "integer"},
            },
            "required": ["query", "project_id"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        project_id = str(kwargs["project_id"])
        if project_id != self._run_context.project.project_id:
            raise ValueError("requested project does not match the authorized run project")
        project = self._run_context.project.model_copy(
            update={"service": kwargs.get("service") or self._run_context.project.service}
        )
        source_types = tuple(
            EvidenceType(value) for value in kwargs.get("source_types", [])
        )
        query = ContextQuery(
            text=str(kwargs["query"]),
            project=project,
            source_types=source_types,
            limit=int(kwargs.get("limit", 10)),
        )
        access = AccessContext(
            tenant_id=self._run_context.project.tenant_id,
            user_id=self._run_context.user_id,
            permissions=self._run_context.permissions,
        )
        bundle = self._engine.search(query, access)
        return bundle.model_dump_json()

