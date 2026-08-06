"""Project-scoped authorization and tool output sanitization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID

from project_lens.domain.models import ProjectRef
from project_lens.runtime.types import ToolCategory, ToolDefinition


@dataclass(frozen=True)
class RunContext:
    run_id: UUID
    trace_id: UUID
    project: ProjectRef
    user_id: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    approvals: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""


class PermissionPolicy:
    """Checks project access, tool permission, and explicit approval for writes."""

    def check(self, definition: ToolDefinition, context: RunContext) -> PermissionDecision:
        project_permission = f"project:{context.project.project_id}:read"
        if project_permission not in context.permissions:
            return PermissionDecision(False, f"missing permission: {project_permission}")
        if definition.required_permission not in context.permissions:
            return PermissionDecision(False, f"missing permission: {definition.required_permission}")
        if definition.category == ToolCategory.WRITE:
            approval = f"tool:{definition.name}:approve"
            if approval not in context.approvals:
                return PermissionDecision(False, f"missing approval: {approval}")
        return PermissionDecision(True)


class RegexSanitizer:
    """Redacts common secrets before tool output enters model context or audit events."""

    _patterns = (
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\bgh[opurs]_[A-Za-z0-9]{16,}\b"),
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*"),
        re.compile(r"(?i)(password|secret|token)\s*[:=]\s*[^\s,;]+"),
    )

    def sanitize(self, content: str) -> str:
        sanitized = content
        for pattern in self._patterns:
            sanitized = pattern.sub("[REDACTED]", sanitized)
        return sanitized

