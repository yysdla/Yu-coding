"""Project space package."""

from project_lens.project_space.models import ProjectSpace, RepositoryRef
from project_lens.project_space.policies import (
    AnswerDepth,
    ChatVisibilityPolicy,
    EffectiveAccessScope,
    ProjectMemberRolePolicy,
    ProjectRuntimeContextResolver,
    ResolvedProjectRuntimeContext,
    RoleKind,
    VisibilityLevel,
    effective_scope_to_audit_dict,
)
from project_lens.project_space.registry import (
    ProjectRegistry,
    load_project_space_json,
    load_project_spaces_from_dir,
    registry_from_local_registrations,
    space_from_local_registration,
)

__all__ = [
    "ProjectRegistry",
    "AnswerDepth",
    "ChatVisibilityPolicy",
    "EffectiveAccessScope",
    "ProjectSpace",
    "ProjectMemberRolePolicy",
    "ProjectRuntimeContextResolver",
    "ResolvedProjectRuntimeContext",
    "RepositoryRef",
    "RoleKind",
    "VisibilityLevel",
    "effective_scope_to_audit_dict",
    "load_project_space_json",
    "load_project_spaces_from_dir",
    "registry_from_local_registrations",
    "space_from_local_registration",
]
