"""One-shot Project Agent ask use case for Hermes/MCP Kernel interface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from project_lens.application.answer_envelope import (
    project_answer_to_envelope,
    recoverable_error,
)
from project_lens.application.run_service import RunService
from project_lens.domain.models import ProjectRef, RunStatus
from project_lens.project_space.registry import ProjectRegistry

# Conservative write/apply intents — avoid matching read-only docs about deploy.
_WRITE_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"帮我部署",
        r"立刻部署",
        r"立即部署",
        r"执行部署",
        r"执行回滚",
        r"帮我回滚",
        r"立刻回滚",
        r"重启服务",
        r"帮我重启",
        r"立刻重启",
        r"创建\s*pr\b",
        r"提\s*pr\b",
        r"提交\s*pr\b",
        r"直接改代码",
        r"帮我改代码",
        r"apply\s+(the\s+)?patch",
        r"\bapply\s+fix\b",
        r"\bdeploy\s+(now|to|production|prod)\b",
        r"\brollback\s+(now|production|prod|the)\b",
        r"\brestart\s+(the\s+)?(service|server|pod|container)\b",
        r"\bcreate\s+(a\s+)?pr\b",
        r"\bopen\s+(a\s+)?pull\s+request\b",
    )
)


@dataclass(frozen=True)
class ProjectAgentAskRequest:
    question: str
    project: ProjectRef
    user_id: str = "mcp-user"
    channel_id: str | None = None
    audience: str = "team"
    mode: str = "read_only"
    format: str = "concise"


class ProjectAgentAskService:
    """Thin adapter: gates -> RunService(read_agent) -> compact envelope.

    Does not query EvidenceIndex / graph / ops stores directly.
    Does not invent facts.
    """

    def __init__(
        self,
        *,
        run_service: RunService,
        project_registry: ProjectRegistry,
    ) -> None:
        if run_service.agent_mode != "read_agent":
            raise ValueError(
                "ProjectAgentAskService requires RunService with agent_mode='read_agent'"
            )
        self._run_service = run_service
        self._registry = project_registry

    @property
    def run_service(self) -> RunService:
        return self._run_service

    @property
    def project_registry(self) -> ProjectRegistry:
        return self._registry

    async def ask(self, request: ProjectAgentAskRequest) -> dict[str, Any]:
        question = request.question.strip()
        if not question:
            return recoverable_error(
                error_code="QUESTION_EMPTY",
                message="question must not be empty",
                retryable=True,
                agent_recovery_hint="Provide a non-empty project question.",
                project=request.project,
            )

        if request.mode not in {"read_only", "readonly", "read"}:
            return recoverable_error(
                error_code="WRITE_ACTION_DENIED",
                message="ProjectLens ask API only supports mode=read_only.",
                retryable=False,
                agent_recovery_hint=(
                    "Retry with mode=read_only. Apply / PR / deploy / rollback / restart "
                    "are permanently disabled."
                ),
                project=request.project,
                allow_apply=False,
            )

        if detects_write_intent(question):
            return recoverable_error(
                error_code="WRITE_ACTION_DENIED",
                message=(
                    "Write/apply/deploy/rollback/restart requests are rejected. "
                    "ProjectLens remains read-only."
                ),
                retryable=False,
                agent_recovery_hint=(
                    "Ask a read-only investigation question instead "
                    "(architecture, files, owners, gaps, change impact). "
                    "Do not request Apply / PR / deploy / rollback / restart."
                ),
                project=request.project,
                allow_apply=False,
                extras={
                    "unknowns": [
                        "该请求属于写操作意图，已拒绝执行；未运行项目调查。"
                    ],
                    "facts": [],
                    "inferences": [],
                    "next_actions": [
                        {
                            "title": "改为只读追问",
                            "description": "说明要了解的模块、入口、变更或缺口，而不是要求系统改代码或部署。",
                            "requires_approval": False,
                        }
                    ],
                },
            )

        space = self._registry.get(request.project.tenant_id, request.project.project_id)
        if space is None:
            return recoverable_error(
                error_code="PROJECT_NOT_FOUND",
                message=(
                    f"ProjectSpace {request.project.tenant_id}/{request.project.project_id} "
                    "is not registered."
                ),
                retryable=False,
                agent_recovery_hint=(
                    "Ask the user which registered ProjectLens project to use, "
                    "or register a ProjectSpace before retrying."
                ),
                project=request.project,
            )

        # Prefer registry ProjectRef defaults (service/env) when caller omitted them.
        project = ProjectRef(
            tenant_id=space.tenant_id,
            project_id=space.project_id,
            service=request.project.service or space.project.service,
            environment=request.project.environment or space.project.environment,
        )

        run = self._run_service.create(
            project=project,
            user_id=request.user_id,
            channel_id=request.channel_id,
            question=question,
        )
        executed = await self._run_service.execute(run.id)
        if executed is None:
            return recoverable_error(
                error_code="RUN_NOT_FOUND",
                message=f"run {run.id} disappeared after create",
                retryable=True,
                agent_recovery_hint="Retry the ask request.",
                project=project,
                extras={"run_id": str(run.id), "trace_id": str(run.trace_id)},
            )

        events = self._run_service.events(executed.id)
        if executed.status == RunStatus.FAILED or executed.answer is None:
            return recoverable_error(
                error_code="INVESTIGATION_FAILED",
                message=executed.error or "project investigation failed without an answer",
                retryable=True,
                agent_recovery_hint=(
                    "Retry later, or ask a narrower read-only question with file/module hints."
                ),
                project=project,
                extras={
                    "run_id": str(executed.id),
                    "trace_id": str(executed.trace_id),
                    "unknowns": [executed.error or "investigation failed"],
                },
            )

        tool_names: tuple[str, ...] = ()
        investigation = getattr(self._run_service, "_investigation", None)
        if investigation is not None:
            tool_names = tuple(getattr(investigation, "last_tool_names", ()) or ())

        return project_answer_to_envelope(
            run=executed,
            answer=executed.answer,
            events=events,
            tool_names=tool_names or None,
            audience=request.audience,
            format=request.format,
        )


def detects_write_intent(question: str) -> bool:
    normalized = " ".join(question.strip().split())
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _WRITE_INTENT_PATTERNS)
