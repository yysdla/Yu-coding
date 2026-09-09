from __future__ import annotations

from uuid import uuid4

from project_lens.application.project_agent_run_detail import (
    ProjectAgentRunDetailRequest,
    ProjectAgentRunDetailService,
)
from project_lens.application.run_service import InMemoryRunRepository, RunService
from project_lens.domain.identity import ActorContext
from project_lens.domain.models import ProjectAnswer, ProjectRef
from project_lens.project_space.policies import (
    ProjectRuntimeContextResolver,
    effective_scope_to_audit_dict,
)


class _Space:
    def __init__(self, project: ProjectRef) -> None:
        self.project = project

    def role_policy_for(self, actor_id: str):  # noqa: ANN001, ANN201
        from project_lens.project_space.policies import (
            DEFAULT_READ_TOOLS,
            AnswerDepth,
            ProjectMemberRolePolicy,
            RoleKind,
            VisibilityLevel,
        )

        return ProjectMemberRolePolicy(
            actor_id=actor_id,
            project=self.project,
            role=RoleKind.DEVELOPER,
            readable_sources=("src/",),
            allowed_tools=DEFAULT_READ_TOOLS,
            answer_depth=AnswerDepth.BALANCED,
            answer_style="team",
            visibility_level=VisibilityLevel.TEAM_SHARED,
        )

    def chat_policy_for(self, chat_id: str):  # noqa: ANN001, ANN201
        from project_lens.project_space.policies import ChatVisibilityPolicy

        return ChatVisibilityPolicy(
            chat_id=chat_id,
            project=self.project,
            chat_type="group",
        )


class _Registry:
    def __init__(self, project: ProjectRef) -> None:
        self._project = project

    def require(self, tenant_id: str, project_id: str):  # noqa: ANN201
        assert tenant_id == self._project.tenant_id
        assert project_id == self._project.project_id
        return _Space(self._project)


def _actor(project: ProjectRef, *, user_id: str = "u1", chat_id: str = "chat-1") -> ActorContext:
    return ActorContext(
        tenant_key=project.tenant_id,
        actor_id=user_id,
        chat_id=chat_id,
        chat_type="group",
        source="test_fixture",
        authenticated=True,
    )


def _runtime_access_for_fake_registry(project: ProjectRef) -> dict[str, object]:
    resolver = ProjectRuntimeContextResolver(
        project_registry=_Registry(project)  # type: ignore[arg-type]
    )
    resolved = resolver.resolve(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        chat_id="chat-1",
        user_id="u1",
        chat_type="group",
        identity_source="test_fixture",
    )
    return effective_scope_to_audit_dict(resolved.effective_scope)


def test_run_detail_rejects_mismatched_actor_or_chat() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    run_service = RunService(InMemoryRunRepository(), agent_mode="hermes")
    run = run_service.create(
        project=project,
        user_id="u1",
        channel_id="chat-1",
        question="what is this",
        runtime_access=_runtime_access_for_fake_registry(project),
        entry_mode="feishu",
    )
    service = ProjectAgentRunDetailService(
        run_service=run_service,
        project_runtime_context_resolver=ProjectRuntimeContextResolver(
            project_registry=_Registry(project)  # type: ignore[arg-type]
        ),
    )

    result = service.run_detail(
        ProjectAgentRunDetailRequest(
            run_id=run.id,
            actor=_actor(project, user_id="u2", chat_id="chat-1"),
        )
    )
    assert result["ok"] is False
    assert result["error_code"] == "ACCESS_DENIED"


def test_run_detail_returns_detail_for_matching_actor() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    run_service = RunService(InMemoryRunRepository(), agent_mode="hermes")
    run = run_service.create(
        project=project,
        user_id="u1",
        channel_id="chat-1",
        question="what is this",
        runtime_access=_runtime_access_for_fake_registry(project),
        entry_mode="feishu",
    )
    run.answer = ProjectAnswer(
        project=project,
        status="identified",
        skill="project_investigation",
        confidence=0.9,
        business_summary="summary",
        technical_summary="tech",
    )
    run_service._repository.add(run)  # type: ignore[attr-defined]
    service = ProjectAgentRunDetailService(
        run_service=run_service,
        project_runtime_context_resolver=ProjectRuntimeContextResolver(
            project_registry=_Registry(project)  # type: ignore[arg-type]
        ),
    )

    result = service.run_detail(ProjectAgentRunDetailRequest(run_id=run.id, actor=_actor(project)))
    assert result["ok"] is True
    assert result["run_id"] == str(run.id)
    assert result["citation_count"] == 0
    assert result["audit_ref"]["allow_apply"] is False


def test_run_detail_route_uses_trusted_headers_when_available() -> None:
    from fastapi.testclient import TestClient
    from project_lens.main import create_app
    from tests.conftest import project_agent_headers

    app = create_app()
    client = TestClient(app)
    resolver = ProjectRuntimeContextResolver(project_registry=app.state.project_registry)
    resolved = resolver.resolve(
        tenant_id="demo",
        project_id="payment",
        chat_id="chat-1",
        user_id="u1",
        chat_type="group",
        identity_source="test_fixture",
    )
    run = app.state.run_service.create(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        user_id="u1",
        channel_id="chat-1",
        question="what is this",
        runtime_access=effective_scope_to_audit_dict(resolved.effective_scope),
        entry_mode="feishu",
    )

    response = client.post(
        f"/api/v1/project-agent/runs/{run.id}/detail",
        json={"audience": "team"},
        headers=project_agent_headers(actor_id="u1", chat_id="chat-1"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["run_id"] == str(run.id)
