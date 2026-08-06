"""Thin ProjectSpace models — Agent Shell stays fixed; project content is pluggable."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project_lens.domain.models import ProjectRef
from project_lens.project_space.policies import (
    ChatVisibilityPolicy,
    ProjectMemberRolePolicy,
)


@dataclass(frozen=True)
class RepositoryRef:
    name: str
    path: Path
    default_branch: str = "main"


@dataclass(frozen=True)
class ProjectSpace:
    """Project-scoped plugin content for the Agent Shell."""

    tenant_id: str
    project_id: str
    display_name: str
    repositories: tuple[RepositoryRef, ...]
    services: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    graph_namespace: str = ""
    memory_namespace: str = ""
    access_scope: str = ""
    file_allowlist: tuple[str, ...] = ("src/", "tests/", "knowledge/")
    default_skill_guides: tuple[str, ...] = ()
    role_policies: tuple[ProjectMemberRolePolicy, ...] = ()
    chat_visibility_policies: tuple[ChatVisibilityPolicy, ...] = ()
    documents_root: Path | None = None

    @property
    def project(self) -> ProjectRef:
        service = self.services[0] if self.services else None
        environment = self.environments[0] if self.environments else None
        return ProjectRef(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            service=service,
            environment=environment,
        )

    @property
    def primary_repository_root(self) -> Path | None:
        if not self.repositories:
            return None
        return self.repositories[0].path

    def role_policy_for(self, actor_id: str) -> ProjectMemberRolePolicy | None:
        for policy in self.role_policies:
            if policy.actor_id == actor_id:
                return policy
        return None

    def chat_policy_for(self, chat_id: str) -> ChatVisibilityPolicy | None:
        for policy in self.chat_visibility_policies:
            if policy.chat_id == chat_id:
                return policy
        return None
