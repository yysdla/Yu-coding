"""Project space package."""

from project_lens.project_space.member_directory import ProjectMember
from project_lens.project_space.models import (
    FeishuChatBindingRef,
    ProjectSpace,
    RepositoryRef,
    SourceConnectorRef,
)
from project_lens.project_space.policies import (
    AnswerDepth,
    ChatVisibilityPolicy,
    EffectiveAccessScope,
    ProjectMemberRolePolicy,
    ProjectRuntimeContextResolver,
    ResolvedProjectRuntimeContext,
    RoleKind,
    VisibilityLevel,
    effective_scope_from_audit_dict,
    effective_scope_to_audit_dict,
)
from project_lens.project_space.registry import (
    ProjectRegistry,
    load_project_space_json,
    load_project_spaces_from_dir,
    registry_from_local_registrations,
    space_from_local_registration,
)
from project_lens.project_space.validation import (
    ProjectSpaceCollectionValidationReport,
    ProjectSpaceValidationIssue,
    ProjectSpaceValidationReport,
    validate_project_space,
    validate_project_spaces,
)

__all__ = [
    "ProjectRegistry",
    "AnswerDepth",
    "ChatVisibilityPolicy",
    "EffectiveAccessScope",
    "FeishuChatBindingRef",
    "ProjectSpace",
    "ProjectSpaceCollectionValidationReport",
    "ProjectSpaceValidationIssue",
    "ProjectSpaceValidationReport",
    "ProjectMember",
    "ProjectMemberRolePolicy",
    "ProjectRuntimeContextResolver",
    "ResolvedProjectRuntimeContext",
    "RepositoryRef",
    "RoleKind",
    "SourceConnectorRef",
    "VisibilityLevel",
    "effective_scope_from_audit_dict",
    "effective_scope_to_audit_dict",
    "load_project_space_json",
    "load_project_spaces_from_dir",
    "registry_from_local_registrations",
    "space_from_local_registration",
    "validate_project_space",
    "validate_project_spaces",
]
