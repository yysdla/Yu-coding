"""Read-only ProjectSpace inspection service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_lens.project_space.models import ProjectSpace
from project_lens.project_space.policies import (
    ProjectRuntimeContextResolver,
    effective_scope_to_audit_dict,
)
from project_lens.project_space.registry import ProjectRegistry
from project_lens.project_space.validation import (
    validate_project_space,
    validate_project_spaces,
)


@dataclass(frozen=True)
class ProjectSpaceScopeInspectRequest:
    tenant_id: str
    project_id: str
    user_id: str
    chat_id: str
    chat_type: str = "group"
    identity_source: str = "member_directory"


class ProjectSpaceInspectService:
    """Expose safe ProjectSpace metadata for debugging project binding issues."""

    def __init__(self, *, project_registry: ProjectRegistry) -> None:
        self._registry = project_registry
        self._resolver = ProjectRuntimeContextResolver(project_registry=project_registry)

    def list_project_spaces(self) -> dict[str, Any]:
        spaces = self._registry.list()
        validation = validate_project_spaces(spaces)
        return {
            "ok": validation.ok,
            "allow_apply": False,
            "project_spaces": [_space_summary(space) for space in spaces],
            "validation": _collection_validation_dict(validation),
        }

    def project_space_detail(self, *, tenant_id: str, project_id: str) -> dict[str, Any]:
        space = self._registry.require(tenant_id, project_id)
        validation = validate_project_space(space)
        return {
            "ok": validation.ok,
            "allow_apply": False,
            "project_space": _space_detail(space),
            "validation": _validation_dict(validation),
        }

    def inspect_scope(self, request: ProjectSpaceScopeInspectRequest) -> dict[str, Any]:
        resolved = self._resolver.resolve(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            user_id=request.user_id,
            chat_id=request.chat_id,
            chat_type=request.chat_type,  # type: ignore[arg-type]
            identity_source=request.identity_source,
        )
        return {
            "ok": True,
            "allow_apply": False,
            "project_space": _space_summary(resolved.project_space),
            "role_policy": {
                "actor_id": resolved.role_policy.actor_id,
                "role": resolved.role_policy.role.value,
                "answer_depth": resolved.role_policy.answer_depth.value,
                "answer_style": resolved.role_policy.answer_style,
                "visibility_level": resolved.role_policy.visibility_level.value,
            },
            "chat_policy": {
                "chat_id": resolved.chat_policy.chat_id,
                "chat_type": resolved.chat_policy.chat_type,
                "visibility_level": resolved.chat_policy.visibility_level.value,
                "allowed_roles": [item.value for item in resolved.chat_policy.allowed_roles],
                "allow_private_details": resolved.chat_policy.allow_private_details,
            },
            "effective_scope": effective_scope_to_audit_dict(resolved.effective_scope),
        }


def _space_summary(space: ProjectSpace) -> dict[str, Any]:
    return {
        "tenant_id": space.tenant_id,
        "project_id": space.project_id,
        "display_name": space.display_name,
        "services": list(space.services),
        "environments": list(space.environments),
        "repository_count": len(space.repositories),
        "source_connector_count": len(space.source_connectors),
        "feishu_chat_binding_count": len(space.feishu_chat_bindings),
        "rag_namespace": space.rag_namespace,
        "graph_namespace": space.graph_namespace,
        "memory_namespace": space.memory_namespace,
        "allow_apply": False,
    }


def _space_detail(space: ProjectSpace) -> dict[str, Any]:
    result = _space_summary(space)
    result.update(
        {
            "repositories": [
                {
                    "name": repo.name,
                    "path": _safe_path(repo.path),
                    "default_branch": repo.default_branch,
                }
                for repo in space.repositories
            ],
            "documents_root": (
                _safe_path(space.documents_root) if space.documents_root is not None else None
            ),
            "file_allowlist": list(space.file_allowlist),
            "default_skill_guides": list(space.default_skill_guides),
            "source_connectors": [
                {
                    "kind": item.kind,
                    "name": item.name,
                    "path": _safe_path(item.path) if item.path is not None else None,
                    "namespace": item.namespace,
                    "metadata_keys": sorted((item.metadata or {}).keys()),
                }
                for item in space.source_connectors
            ],
            "feishu_chat_bindings": [
                {
                    "tenant_key": item.tenant_key,
                    "chat_id": item.chat_id,
                    "visibility": item.visibility,
                }
                for item in space.feishu_chat_bindings
            ],
        }
    )
    return result


def _validation_dict(report: Any) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "errors": [
            {
                "code": issue.code,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in report.errors
        ],
        "warnings": [
            {
                "code": issue.code,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in getattr(report, "warnings", ())
        ],
    }


def _collection_validation_dict(report: Any) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "errors": [
            {
                "code": issue.code,
                "message": issue.message,
                "severity": issue.severity,
            }
            for issue in report.errors
        ],
    }


def _safe_path(path: Path) -> str:
    return str(path)
