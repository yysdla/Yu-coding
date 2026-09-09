"""Thin ProjectSpace models — Agent Shell stays fixed; project content is pluggable."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_lens.domain.models import ProjectRef
from project_lens.project_space.member_directory import ProjectMember
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
class SourceConnectorRef:
    """ProjectSpace-owned source connector metadata.

    This is a manifest-level reference only. Runtime access still goes through
    ProjectLens gateways and indexes, never directly through the Agent runtime.
    """

    kind: str
    name: str
    path: Path | None = None
    namespace: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class FeishuChatBindingRef:
    """Manifest-level Feishu chat binding for a ProjectSpace."""

    tenant_key: str
    chat_id: str
    visibility: str = "team_shared"


@dataclass(frozen=True)
class ProjectSpace:
    """Project-scoped plugin content for the Agent Shell."""

    tenant_id: str
    project_id: str
    display_name: str
    repositories: tuple[RepositoryRef, ...]
    services: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    rag_namespace: str = ""
    graph_namespace: str = ""
    memory_namespace: str = ""
    access_scope: str = ""
    file_allowlist: tuple[str, ...] = ("src/", "tests/", "knowledge/")
    public_sources: tuple[str, ...] = ()
    default_skill_guides: tuple[str, ...] = ()
    members: tuple[ProjectMember, ...] = ()
    role_policies: tuple[ProjectMemberRolePolicy, ...] = ()
    chat_visibility_policies: tuple[ChatVisibilityPolicy, ...] = ()
    documents_root: Path | None = None
    source_connectors: tuple[SourceConnectorRef, ...] = ()
    feishu_chat_bindings: tuple[FeishuChatBindingRef, ...] = ()

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
