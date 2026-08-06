"""Resolve an incoming run to an explicitly registered project."""

from __future__ import annotations

from collections.abc import Iterable

from project_lens.domain.models import ProjectRef
from project_lens.workflow.models import ProjectRegistration, ResolvedProject


class ProjectResolutionError(ValueError):
    pass


def _normalize_registration(
    item: ProjectRef | ProjectRegistration,
) -> ProjectRegistration:
    if isinstance(item, ProjectRegistration):
        return item
    return ProjectRegistration(
        project=item,
        access_scope=f"project:{item.project_id}:read",
    )


class ProjectResolver:
    def __init__(self, projects: Iterable[ProjectRef | ProjectRegistration]) -> None:
        self._projects = tuple(_normalize_registration(item) for item in projects)

    def resolve(self, requested: ProjectRef) -> ResolvedProject:
        matches = [
            registration
            for registration in self._projects
            if registration.project.tenant_id == requested.tenant_id
            and registration.project.project_id == requested.project_id
        ]
        if requested.service:
            matches = [
                registration
                for registration in matches
                if registration.project.service == requested.service
            ]
        if requested.environment:
            matches = [
                registration
                for registration in matches
                if registration.project.environment == requested.environment
            ]
        if not matches:
            raise ProjectResolutionError(
                "project is not registered or the requested service/environment does not match"
            )
        if len(matches) > 1:
            raise ProjectResolutionError("project context is ambiguous")
        registration = matches[0]
        return ResolvedProject(
            project=registration.project,
            access_scope=registration.access_scope,
            resolution="explicit_project_reference",
        )
