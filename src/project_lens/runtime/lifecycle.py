"""Append-only lifecycle hooks for audit, replay, and Feishu progress."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from project_lens.domain.models import ProjectRef
from project_lens.runtime.events import AgentEvent, AgentEventType, EventSink


class LifecycleEventType(StrEnum):
    """Harness lifecycle names from agent-harness-design.md."""

    RUN_CREATED = "run.created"
    RUN_RESOLVED = "run.resolved"
    CONTEXT_COLLECTED = "context.collected"
    CONTEXT_PROMPT_RENDERED = "context.prompt_rendered"
    MODEL_PROVIDER_FAILED = "model.provider_failed"
    ANALYSIS_COMPLETED = "analysis.completed"
    VERIFICATION_COMPLETED = "verification.completed"
    ANSWER_COMPOSED = "answer.composed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_COMPLETED = "tool.completed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    MEMORY_PROPOSED = "memory.proposed"
    MEMORY_APPROVED = "memory.approved"
    MEMORY_REJECTED = "memory.rejected"
    ENGINEERING_PROPOSED = "engineering.proposed"
    ENGINEERING_VALIDATED = "engineering.validated"
    DOC_SYNC_STARTED = "doc_sync.started"
    DOC_SYNC_COMPLETED = "doc_sync.completed"
    OPS_SIGNAL_QUERIED = "ops_signal.queried"
    COMPRESSION_TRIGGERED = "compression.triggered"
    COMPRESSION_COMPLETED = "compression.completed"
    SESSION_CREATED = "session.created"
    SESSION_LOADED = "session.loaded"
    SESSION_SAVED = "session.saved"


# Run-scoped hooks mirror into append-only AgentEvents without overloading status changes.
_RUN_SCOPED_LIFECYCLE = frozenset(
    {
        LifecycleEventType.RUN_CREATED,
        LifecycleEventType.RUN_RESOLVED,
        LifecycleEventType.CONTEXT_COLLECTED,
        LifecycleEventType.CONTEXT_PROMPT_RENDERED,
        LifecycleEventType.MODEL_PROVIDER_FAILED,
        LifecycleEventType.ANALYSIS_COMPLETED,
        LifecycleEventType.VERIFICATION_COMPLETED,
        LifecycleEventType.ANSWER_COMPOSED,
        LifecycleEventType.OPS_SIGNAL_QUERIED,
        LifecycleEventType.ENGINEERING_PROPOSED,
        LifecycleEventType.ENGINEERING_VALIDATED,
        LifecycleEventType.TOOL_REQUESTED,
        LifecycleEventType.TOOL_COMPLETED,
        LifecycleEventType.COMPRESSION_TRIGGERED,
        LifecycleEventType.COMPRESSION_COMPLETED,
        LifecycleEventType.MEMORY_PROPOSED,
        LifecycleEventType.APPROVAL_REQUESTED,
    }
)


@dataclass(frozen=True)
class LifecycleEvent:
    type: LifecycleEventType
    payload: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: UUID | None = None
    trace_id: UUID | None = None
    project: ProjectRef | None = None


class LifecycleBus:
    """Sync, append-only hook bus. Optionally mirrors run-scoped hooks to EventSink."""

    def __init__(self, event_sink: EventSink | None = None) -> None:
        self._events: list[LifecycleEvent] = []
        self._event_sink = event_sink

    def emit(
        self,
        event_type: LifecycleEventType | str,
        *,
        payload: dict[str, Any] | None = None,
        run_id: UUID | None = None,
        trace_id: UUID | None = None,
        project: ProjectRef | None = None,
    ) -> LifecycleEvent:
        typed = (
            event_type
            if isinstance(event_type, LifecycleEventType)
            else LifecycleEventType(event_type)
        )
        event = LifecycleEvent(
            type=typed,
            payload=dict(payload or {}),
            run_id=run_id,
            trace_id=trace_id,
            project=project,
        )
        self._events.append(event)
        return event

    async def emit_async(
        self,
        event_type: LifecycleEventType | str,
        *,
        payload: dict[str, Any] | None = None,
        run_id: UUID | None = None,
        trace_id: UUID | None = None,
        project: ProjectRef | None = None,
    ) -> LifecycleEvent:
        event = self.emit(
            event_type,
            payload=payload,
            run_id=run_id,
            trace_id=trace_id,
            project=project,
        )
        if (
            self._event_sink is not None
            and event.run_id is not None
            and event.trace_id is not None
            and event.type in _RUN_SCOPED_LIFECYCLE
        ):
            agent_type = (
                AgentEventType.RUN_STARTED
                if event.type == LifecycleEventType.RUN_CREATED
                else AgentEventType.TOOL_STARTED
                if event.type == LifecycleEventType.TOOL_REQUESTED
                else AgentEventType.TOOL_COMPLETED
                if event.type == LifecycleEventType.TOOL_COMPLETED
                else AgentEventType.LIFECYCLE
            )
            await self._event_sink.emit(
                AgentEvent(
                    run_id=event.run_id,
                    trace_id=event.trace_id,
                    type=agent_type,
                    payload={
                        "lifecycle": event.type.value,
                        **event.payload,
                    },
                )
            )
        return event

    def all(self) -> tuple[LifecycleEvent, ...]:
        return tuple(self._events)

    def for_run(self, run_id: UUID) -> tuple[LifecycleEvent, ...]:
        return tuple(event for event in self._events if event.run_id == run_id)

    def of_type(self, event_type: LifecycleEventType) -> tuple[LifecycleEvent, ...]:
        return tuple(event for event in self._events if event.type == event_type)
