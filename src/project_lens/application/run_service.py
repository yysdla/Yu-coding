"""Agent run lifecycle use cases."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from project_lens.agent.investigation import ProjectInvestigationAgent
from project_lens.config import settings
from project_lens.domain.conversation import ConversationSession
from project_lens.domain.memory import ProjectMemory
from project_lens.domain.models import AgentRun, ProjectAnswer, ProjectRef, RunStatus, utc_now
from project_lens.runtime.events import AgentEvent, AgentEventType, EventSink, InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.workflow.orchestrator import ProjectWorkflow
from project_lens.workflow.task_scratchpad import (
    compress_events_to_scratchpad,
    scratchpad_to_session_dict,
)


class RunRepository(Protocol):
    def add(self, run: AgentRun) -> None: ...

    def get(self, run_id: UUID) -> AgentRun | None: ...


class EventQuerySink(EventSink, Protocol):
    def for_run(self, run_id: UUID) -> tuple[AgentEvent, ...]: ...


class InMemoryRunRepository:
    def __init__(self) -> None:
        self._runs: dict[UUID, AgentRun] = {}

    def add(self, run: AgentRun) -> None:
        self._runs[run.id] = run.model_copy(deep=True)

    def get(self, run_id: UUID) -> AgentRun | None:
        run = self._runs.get(run_id)
        return run.model_copy(deep=True) if run else None


class RunService:
    def __init__(
        self,
        repository: RunRepository,
        workflow: ProjectWorkflow | None = None,
        event_sink: EventQuerySink | None = None,
        lifecycle: LifecycleBus | None = None,
        *,
        investigation_agent: ProjectInvestigationAgent | None = None,
        agent_mode: str | None = None,
    ) -> None:
        self._repository = repository
        self._workflow = workflow
        self._investigation = investigation_agent
        self._agent_mode = (agent_mode or settings.agent_mode or "workflow").strip().lower()
        self._events = event_sink or InMemoryEventSink()
        self._lifecycle = lifecycle or LifecycleBus(event_sink=self._events)

    @property
    def lifecycle(self) -> LifecycleBus:
        return self._lifecycle

    @property
    def agent_mode(self) -> str:
        return self._agent_mode

    def create(
        self,
        *,
        project: ProjectRef,
        user_id: str,
        question: str,
        channel_id: str | None = None,
        runtime_access: dict[str, object] | None = None,
        entry_mode: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            project=project,
            user_id=user_id,
            channel_id=channel_id,
            question=question,
        )
        self._repository.add(run)
        payload: dict[str, object] = {
            "user_id": user_id,
            "channel_id": channel_id,
            "question_preview": question[:200],
            "agent_mode": self._agent_mode,
        }
        if runtime_access is not None:
            payload["runtime_access"] = runtime_access
        if entry_mode is not None:
            payload["entry_mode"] = entry_mode
        self._lifecycle.emit(
            LifecycleEventType.RUN_CREATED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=project,
            payload=payload,
        )
        return run

    def get(self, run_id: UUID) -> AgentRun | None:
        return self._repository.get(run_id)

    def events(self, run_id: UUID) -> tuple[AgentEvent, ...]:
        return self._events.for_run(run_id)

    async def execute(
        self,
        run_id: UUID,
        *,
        session: ConversationSession | None = None,
        memories: tuple[ProjectMemory, ...] = (),
    ) -> AgentRun | None:
        run = self._repository.get(run_id)
        if run is None:
            return None
        use_read_agent = self._agent_mode == "read_agent" and self._investigation is not None
        if not use_read_agent and self._workflow is None:
            raise RuntimeError("run workflow is not configured")
        if run.status not in {RunStatus.ACCEPTED, RunStatus.FAILED}:
            raise ValueError(f"run cannot be executed from status {run.status}")

        async def transition(status: RunStatus, payload: dict[str, object]) -> None:
            nonlocal run
            run = run.model_copy(update={"status": status, "updated_at": utc_now()})
            self._repository.add(run)
            await self._events.emit(
                AgentEvent(
                    run_id=run.id,
                    trace_id=run.trace_id,
                    type=AgentEventType.RUN_STATUS_CHANGED,
                    payload={"status": status.value, **payload},
                )
            )

        try:
            if use_read_agent:
                assert self._investigation is not None
                answer = await self._investigation.execute(
                    run,
                    transition,
                    session=session,
                    memories=memories,
                )
            else:
                assert self._workflow is not None
                answer = await self._workflow.execute(
                    run,
                    transition,
                    session=session,
                    memories=memories,
                )
            run = self._complete(run, answer)
            prior = None
            if self._workflow is not None and self._workflow.last_context_pack is not None:
                prior = self._workflow.last_context_pack.task_state
            task_state = compress_events_to_scratchpad(
                self._events.for_run(run.id),
                prior=prior,
                answer=answer,
            )
            if task_state is not None and self._workflow is not None:
                self._workflow.set_last_task_state(task_state)
            await self._events.emit(
                AgentEvent(
                    run_id=run.id,
                    trace_id=run.trace_id,
                    type=AgentEventType.RUN_COMPLETED,
                    payload={
                        "status": run.status.value,
                        "claim_count": len(answer.claims),
                        "evidence_count": len(answer.evidence),
                        "agent_mode": self._agent_mode,
                        "task_scratchpad": scratchpad_to_session_dict(task_state),
                    },
                )
            )
        except Exception as exc:
            run = run.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "error": str(exc),
                    "updated_at": utc_now(),
                }
            )
            self._repository.add(run)
            await self._events.emit(
                AgentEvent(
                    run_id=run.id,
                    trace_id=run.trace_id,
                    type=AgentEventType.RUN_FAILED,
                    payload={"status": run.status.value, "error": str(exc)},
                )
            )
        return run

    def _complete(self, run: AgentRun, answer: ProjectAnswer) -> AgentRun:
        completed = run.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "answer": answer,
                "error": None,
                "updated_at": utc_now(),
            }
        )
        self._repository.add(completed)
        return completed
