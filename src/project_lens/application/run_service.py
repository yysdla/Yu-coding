"""Agent run lifecycle use cases."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from project_lens.config import settings
from project_lens.context.history_store import HistoryMemoryStore
from project_lens.domain.models import AgentRun, ProjectAnswer, ProjectRef, RunStatus, utc_now
from project_lens.runtime.events import AgentEvent, AgentEventType, EventSink, InMemoryEventSink
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType


class RunRepository(Protocol):
    def add(self, run: AgentRun) -> None: ...

    def get(self, run_id: UUID) -> AgentRun | None: ...

    def list_recent(self, project: ProjectRef, *, limit: int = 100) -> tuple[AgentRun, ...]: ...


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

    def list_recent(self, project: ProjectRef, *, limit: int = 100) -> tuple[AgentRun, ...]:
        runs = [
            run for run in self._runs.values()
            if run.project.tenant_id == project.tenant_id
            and run.project.project_id == project.project_id
        ]
        runs.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(run.model_copy(deep=True) for run in runs[: max(1, int(limit))])


class RunService:
    """Hermes-only run lifecycle. Legacy workflow / read_agent execute paths are removed."""

    def __init__(
        self,
        repository: RunRepository,
        event_sink: EventQuerySink | None = None,
        lifecycle: LifecycleBus | None = None,
        *,
        agent_mode: str | None = None,
        history_store: HistoryMemoryStore | None = None,
    ) -> None:
        self._repository = repository
        self._agent_mode = (agent_mode or settings.agent_mode or "hermes").strip().lower()
        if self._agent_mode in {"workflow", "read_agent"}:
            raise ValueError(
                f"agent_mode={self._agent_mode!r} is retired; use hermes"
            )
        self._events = event_sink or InMemoryEventSink()
        self._lifecycle = lifecycle or LifecycleBus(event_sink=self._events)
        self._history_store = history_store

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
        runtime: str | None = None,
        hermes_loop_id: UUID | None = None,
        context_snapshot_id: UUID | None = None,
        context_hash: str | None = None,
    ) -> AgentRun:
        run = AgentRun(
            project=project,
            user_id=user_id,
            channel_id=channel_id,
            question=question,
            runtime_access=runtime_access,
            runtime=(runtime or self._agent_mode),
            entry_mode=entry_mode,
            hermes_loop_id=hermes_loop_id,
            context_snapshot_id=context_snapshot_id,
            context_hash=context_hash,
        )
        self._repository.add(run)
        payload: dict[str, object] = {
            "user_id": user_id,
            "channel_id": channel_id,
            "question_preview": question[:200],
            "agent_mode": self._agent_mode,
            "runtime": run.runtime,
        }
        if runtime_access is not None:
            payload["runtime_access"] = runtime_access
        if entry_mode is not None:
            payload["entry_mode"] = entry_mode
        if hermes_loop_id is not None:
            payload["hermes_loop_id"] = str(hermes_loop_id)
        if context_snapshot_id is not None:
            payload["context_snapshot_id"] = str(context_snapshot_id)
        if context_hash is not None:
            payload["context_hash"] = context_hash
        self._lifecycle.emit(
            LifecycleEventType.RUN_CREATED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=project,
            payload=payload,
        )
        self._persist_history(run)
        return run

    def get(self, run_id: UUID) -> AgentRun | None:
        return self._repository.get(run_id)

    def events(self, run_id: UUID) -> tuple[AgentEvent, ...]:
        return self._events.for_run(run_id)

    async def complete_external(
        self,
        run_id: UUID,
        *,
        answer: ProjectAnswer | None,
        error: str | None = None,
        agent_mode: str = "hermes",
        hermes_loop_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AgentRun | None:
        """Persist a result produced by an external Agent runtime such as Hermes."""
        run = self._repository.get(run_id)
        if run is None:
            return None
        update: dict[str, object] = {"updated_at": utc_now()}
        if hermes_loop_id is not None:
            update["hermes_loop_id"] = hermes_loop_id
        if answer is not None and error is None:
            completed = run.model_copy(
                update={
                    **update,
                    "status": RunStatus.COMPLETED,
                    "answer": answer,
                    "error": None,
                }
            )
        else:
            completed = run.model_copy(
                update={
                    **update,
                    "status": RunStatus.FAILED,
                    "answer": None,
                    "error": error or "external agent failed",
                }
            )
        self._repository.add(completed)
        payload: dict[str, object] = {
            "status": completed.status.value,
            "agent_mode": agent_mode,
            "runtime": completed.runtime,
            "claim_count": len(answer.claims) if answer is not None else 0,
            "evidence_count": len(answer.evidence) if answer is not None else 0,
        }
        if metadata:
            payload.update(metadata)
        await self._events.emit(
            AgentEvent(
                run_id=completed.id,
                trace_id=completed.trace_id,
                type=(
                    AgentEventType.RUN_COMPLETED
                    if completed.status == RunStatus.COMPLETED
                    else AgentEventType.RUN_FAILED
                ),
                payload=(
                    payload
                    if completed.status == RunStatus.COMPLETED
                    else {**payload, "error": completed.error}
                ),
            )
        )
        self._persist_history(completed)
        return completed

    async def execute(self, run_id: UUID, **_: object) -> AgentRun | None:
        """Legacy entrypoint. Hermes runs must use HermesRuntimeService."""
        run = self._repository.get(run_id)
        if run is None:
            return None
        raise RuntimeError(
            "Hermes runs must be executed through HermesRuntimeService "
            "(legacy workflow / read_agent execute paths are removed)"
        )

    def _persist_history(self, run: AgentRun) -> None:
        if self._history_store is None:
            return
        events = self._events.for_run(run.id) if hasattr(self._events, "for_run") else ()
        self._history_store.upsert_run(run, events)
