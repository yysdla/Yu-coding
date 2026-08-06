"""Role-aware ProjectSpace policies.

These models are deliberately project-scoped.  Hermes may drive the agent loop,
but ProjectLens owns what can be read, how much can be shown, and which role
policy is active before answer generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from project_lens.domain.models import ProjectRef

if TYPE_CHECKING:  # pragma: no cover - import only for static typing
    from project_lens.project_space.models import ProjectSpace
    from project_lens.project_space.registry import ProjectRegistry


DEFAULT_READ_TOOLS: tuple[str, ...] = (
    "search_context",
    "read_project_file",
    "query_graph",
    "list_knowledge_gaps",
)


class RoleKind(StrEnum):
    DEVELOPER = "developer"
    OPS = "ops"
    QA = "qa"
    PRODUCT = "product"
    MANAGER = "manager"
    ONBOARDING = "onboarding"
    GUEST = "guest"


class AnswerDepth(StrEnum):
    BRIEF = "brief"
    BALANCED = "balanced"
    DETAILED = "detailed"


class VisibilityLevel(StrEnum):
    PRIVATE = "private"
    TEAM_SHARED = "team_shared"


@dataclass(frozen=True)
class ProjectMemberRolePolicy:
    actor_id: str
    project: ProjectRef
    role: RoleKind
    readable_sources: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    answer_depth: AnswerDepth = AnswerDepth.BALANCED
    answer_style: str = "team"
    visibility_level: VisibilityLevel = VisibilityLevel.TEAM_SHARED
    escalation_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatVisibilityPolicy:
    chat_id: str
    project: ProjectRef
    visibility_level: VisibilityLevel = VisibilityLevel.TEAM_SHARED
    allowed_roles: tuple[RoleKind, ...] = ()
    readable_sources: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    answer_depth: AnswerDepth | None = None
    answer_style: str | None = None


@dataclass(frozen=True)
class EffectiveAccessScope:
    project: ProjectRef
    actor_id: str
    chat_id: str
    role: RoleKind
    readable_sources: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    forbidden_sources: tuple[str, ...]
    answer_depth: AnswerDepth
    answer_style: str
    visibility_level: VisibilityLevel


def effective_scope_to_audit_dict(scope: EffectiveAccessScope) -> dict[str, object]:
    """Serialize capability bounds for session/lifecycle audit only.

    Never include ProjectSpace, Evidence, prompts, file bodies, or secrets.
    """

    return {
        "tenant_id": scope.project.tenant_id,
        "project_id": scope.project.project_id,
        "actor_id": scope.actor_id,
        "chat_id": scope.chat_id,
        "role": scope.role.value,
        "visibility_level": scope.visibility_level.value,
        "answer_depth": scope.answer_depth.value,
        "answer_style": scope.answer_style,
        "allowed_tools": list(scope.allowed_tools),
        "readable_sources": list(scope.readable_sources),
        "forbidden_sources": list(scope.forbidden_sources),
    }


@dataclass(frozen=True)
class ResolvedProjectRuntimeContext:
    project_space: ProjectSpace
    actor_id: str
    channel_id: str
    role_policy: ProjectMemberRolePolicy
    chat_policy: ChatVisibilityPolicy
    effective_scope: EffectiveAccessScope

    @property
    def project(self) -> ProjectRef:
        return self.project_space.project


class ProjectRuntimeContextResolver:
    """Resolve ProjectSpace + user role + chat visibility before agent generation."""

    def __init__(self, *, project_registry: ProjectRegistry) -> None:
        self._project_registry = project_registry

    def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        chat_id: str,
        user_id: str,
    ) -> ResolvedProjectRuntimeContext:
        space = self._project_registry.require(tenant_id, project_id)
        role_policy = space.role_policy_for(user_id) or default_member_role_policy(
            space=space,
            actor_id=user_id,
        )
        chat_policy = space.chat_policy_for(chat_id) or default_chat_visibility_policy(
            space=space,
            chat_id=chat_id,
        )
        if chat_policy.allowed_roles and role_policy.role not in chat_policy.allowed_roles:
            raise PermissionError(
                f"role {role_policy.role.value} is not allowed in chat {chat_id}"
            )
        effective_scope = combine_role_and_chat_policy(
            role_policy=role_policy,
            chat_policy=chat_policy,
        )
        return ResolvedProjectRuntimeContext(
            project_space=space,
            actor_id=user_id,
            channel_id=chat_id,
            role_policy=role_policy,
            chat_policy=chat_policy,
            effective_scope=effective_scope,
        )


def default_member_role_policy(
    *,
    space: ProjectSpace,
    actor_id: str,
) -> ProjectMemberRolePolicy:
    return ProjectMemberRolePolicy(
        actor_id=actor_id,
        project=space.project,
        role=RoleKind.GUEST,
        readable_sources=space.file_allowlist,
        allowed_tools=DEFAULT_READ_TOOLS,
        answer_depth=AnswerDepth.BRIEF,
        answer_style="team",
        visibility_level=VisibilityLevel.TEAM_SHARED,
    )


def default_chat_visibility_policy(
    *,
    space: ProjectSpace,
    chat_id: str,
) -> ChatVisibilityPolicy:
    return ChatVisibilityPolicy(
        chat_id=chat_id,
        project=space.project,
        visibility_level=VisibilityLevel.TEAM_SHARED,
        readable_sources=space.file_allowlist,
        allowed_tools=DEFAULT_READ_TOOLS,
    )


def combine_role_and_chat_policy(
    *,
    role_policy: ProjectMemberRolePolicy,
    chat_policy: ChatVisibilityPolicy,
) -> EffectiveAccessScope:
    return EffectiveAccessScope(
        project=role_policy.project,
        actor_id=role_policy.actor_id,
        chat_id=chat_policy.chat_id,
        role=role_policy.role,
        readable_sources=_intersect_or_inherit(
            role_policy.readable_sources,
            chat_policy.readable_sources,
        ),
        allowed_tools=_intersect_or_inherit(
            role_policy.allowed_tools,
            chat_policy.allowed_tools,
        ),
        forbidden_sources=_dedupe((*chat_policy.forbidden_sources, *role_policy.forbidden_sources)),
        answer_depth=_more_conservative_depth(
            role_policy.answer_depth,
            chat_policy.answer_depth,
        ),
        answer_style=chat_policy.answer_style or role_policy.answer_style,
        visibility_level=_more_conservative_visibility(
            role_policy.visibility_level,
            chat_policy.visibility_level,
        ),
    )


def _intersect_or_inherit(
    primary: tuple[str, ...],
    secondary: tuple[str, ...],
) -> tuple[str, ...]:
    if not primary:
        return secondary
    if not secondary:
        return primary
    allowed = set(secondary)
    return tuple(item for item in primary if item in allowed)


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return tuple(result)


def _more_conservative_depth(
    role_depth: AnswerDepth,
    chat_depth: AnswerDepth | None,
) -> AnswerDepth:
    if chat_depth is None:
        return role_depth
    rank = {
        AnswerDepth.BRIEF: 0,
        AnswerDepth.BALANCED: 1,
        AnswerDepth.DETAILED: 2,
    }
    return role_depth if rank[role_depth] <= rank[chat_depth] else chat_depth


def _more_conservative_visibility(
    role_visibility: VisibilityLevel,
    chat_visibility: VisibilityLevel,
) -> VisibilityLevel:
    rank = {
        VisibilityLevel.PRIVATE: 0,
        VisibilityLevel.TEAM_SHARED: 1,
    }
    return (
        role_visibility
        if rank[role_visibility] <= rank[chat_visibility]
        else chat_visibility
    )

