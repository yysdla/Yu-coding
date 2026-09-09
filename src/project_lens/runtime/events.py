"""Runtime events used by Feishu progress updates and durable audit storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID


class AgentEventType(StrEnum):
    RUN_STARTED = "run_started"
    RUN_STATUS_CHANGED = "run_status_changed"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONDED = "model_responded"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_DENIED = "tool_denied"
    LIFECYCLE = "lifecycle"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RISK_DETECTED = "risk_detected"
    RISK_UPDATED = "risk_updated"
    RISK_SEVERITY_CHANGED = "risk_severity_changed"
    RISK_RESOLVED = "risk_resolved"


@dataclass(frozen=True)
class AgentEvent:
    run_id: UUID
    trace_id: UUID
    type: AgentEventType
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...


class NullEventSink:
    async def emit(self, event: AgentEvent) -> None:
        del event


class InMemoryEventSink:
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    def for_run(self, run_id: UUID) -> tuple[AgentEvent, ...]:
        return tuple(event for event in self.events if event.run_id == run_id)
