"""ProjectSpace manifest validation.

Validation keeps ProjectSpace pluggable: a new project should fail fast when
its package is incomplete or unsafe, instead of failing inside Feishu/Hermes
after the user asks a question.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project_lens.project_space.models import ProjectSpace


@dataclass(frozen=True)
class ProjectSpaceValidationIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ProjectSpaceValidationReport:
    project_id: str
    tenant_id: str
    issues: tuple[ProjectSpaceValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ProjectSpaceValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ProjectSpaceValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def has_code(self, code: str) -> bool:
        return any(issue.code == code for issue in self.issues)


@dataclass(frozen=True)
class ProjectSpaceCollectionValidationReport:
    issues: tuple[ProjectSpaceValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[ProjectSpaceValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def ok(self) -> bool:
        return not self.errors

    def has_code(self, code: str) -> bool:
        return any(issue.code == code for issue in self.issues)


def validate_project_space(space: ProjectSpace) -> ProjectSpaceValidationReport:
    issues: list[ProjectSpaceValidationIssue] = []

    if not space.repositories:
        issues.append(
            ProjectSpaceValidationIssue(
                code="missing_repository",
                message="ProjectSpace must declare at least one repository.",
            )
        )
    for repo in space.repositories:
        if not repo.path.exists():
            issues.append(
                ProjectSpaceValidationIssue(
                    code="missing_repository_path",
                    message=f"Repository path does not exist: {repo.path}",
                )
            )

    if space.documents_root is not None and not space.documents_root.exists():
        issues.append(
            ProjectSpaceValidationIssue(
                code="missing_documents_root",
                message=f"Documents root does not exist: {space.documents_root}",
            )
        )

    for item in space.file_allowlist:
        if _is_unsafe_allowlist_entry(item):
            issues.append(
                ProjectSpaceValidationIssue(
                    code="unsafe_file_allowlist",
                    message=f"Unsafe file allowlist entry: {item}",
                )
            )

    if not space.public_sources:
        issues.append(
            ProjectSpaceValidationIssue(
                code="missing_public_sources",
                message=(
                    "ProjectSpace must declare public_sources for guest/deny-by-default access."
                ),
            )
        )

    seen_members: set[str] = set()
    for member in space.members:
        if member.actor_id in seen_members:
            issues.append(
                ProjectSpaceValidationIssue(
                    code="duplicate_member",
                    message=f"Duplicate ProjectSpace member: {member.actor_id}",
                )
            )
        seen_members.add(member.actor_id)
        if not member.roles:
            issues.append(
                ProjectSpaceValidationIssue(
                    code="empty_member_roles",
                    message=f"Member {member.actor_id} must declare at least one role.",
                )
            )

    if not space.rag_namespace.strip():
        issues.append(
            ProjectSpaceValidationIssue(
                code="missing_rag_namespace",
                message="ProjectSpace must declare a rag_namespace.",
            )
        )
    if not space.graph_namespace.strip():
        issues.append(
            ProjectSpaceValidationIssue(
                code="missing_graph_namespace",
                message="ProjectSpace must declare a graph_namespace.",
            )
        )
    if not space.memory_namespace.strip():
        issues.append(
            ProjectSpaceValidationIssue(
                code="missing_memory_namespace",
                message="ProjectSpace must declare a memory_namespace.",
            )
        )

    seen_chats: set[tuple[str, str]] = set()
    for binding in space.feishu_chat_bindings:
        key = (binding.tenant_key, binding.chat_id)
        if key in seen_chats:
            issues.append(
                ProjectSpaceValidationIssue(
                    code="duplicate_feishu_chat_binding",
                    message=(
                        "Duplicate Feishu chat binding in ProjectSpace: "
                        f"{binding.tenant_key}/{binding.chat_id}"
                    ),
                )
            )
        seen_chats.add(key)

    return ProjectSpaceValidationReport(
        tenant_id=space.tenant_id,
        project_id=space.project_id,
        issues=tuple(issues),
    )


def validate_project_spaces(
    spaces: tuple[ProjectSpace, ...],
) -> ProjectSpaceCollectionValidationReport:
    issues: list[ProjectSpaceValidationIssue] = []
    seen_projects: set[tuple[str, str]] = set()
    seen_feishu_chats: dict[tuple[str, str], str] = {}
    namespaces: dict[str, dict[str, str]] = {
        "rag": {},
        "graph": {},
        "memory": {},
    }

    for space in spaces:
        issues.extend(validate_project_space(space).issues)
        project_key = (space.tenant_id, space.project_id)
        project_label = f"{space.tenant_id}/{space.project_id}"
        if project_key in seen_projects:
            issues.append(
                ProjectSpaceValidationIssue(
                    code="duplicate_project_space",
                    message=f"Duplicate ProjectSpace: {project_label}",
                )
            )
        seen_projects.add(project_key)

        for binding in space.feishu_chat_bindings:
            binding_key = (binding.tenant_key, binding.chat_id)
            owner = seen_feishu_chats.get(binding_key)
            if owner is not None and owner != project_label:
                issues.append(
                    ProjectSpaceValidationIssue(
                        code="duplicate_feishu_chat_binding",
                        message=(
                            "Feishu chat binding is shared by multiple projects: "
                            f"{binding.tenant_key}/{binding.chat_id}"
                        ),
                    )
                )
            seen_feishu_chats[binding_key] = project_label

        _check_namespace(
            namespaces["rag"],
            namespace=space.rag_namespace,
            project_label=project_label,
            code="duplicate_rag_namespace",
            issues=issues,
        )
        _check_namespace(
            namespaces["graph"],
            namespace=space.graph_namespace,
            project_label=project_label,
            code="duplicate_graph_namespace",
            issues=issues,
        )
        _check_namespace(
            namespaces["memory"],
            namespace=space.memory_namespace,
            project_label=project_label,
            code="duplicate_memory_namespace",
            issues=issues,
        )

    return ProjectSpaceCollectionValidationReport(issues=tuple(issues))


def _check_namespace(
    owners: dict[str, str],
    *,
    namespace: str,
    project_label: str,
    code: str,
    issues: list[ProjectSpaceValidationIssue],
) -> None:
    if not namespace.strip():
        return
    owner = owners.get(namespace)
    if owner is not None and owner != project_label:
        issues.append(
            ProjectSpaceValidationIssue(
                code=code,
                message=f"Namespace {namespace} is shared by {owner} and {project_label}.",
            )
        )
    owners[namespace] = project_label


def _is_unsafe_allowlist_entry(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return True
    path = Path(value)
    return path.is_absolute() or ".." in path.parts
