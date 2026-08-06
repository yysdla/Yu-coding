from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import pytest

from project_lens.domain.models import ProjectRef
from project_lens.runtime.events import AgentEventType, InMemoryEventSink
from project_lens.runtime.loop import AgentLoop
from project_lens.runtime.security import RunContext
from project_lens.runtime.tools import BaseTool, ToolRegistry
from project_lens.runtime.types import (
    FinishReason,
    Message,
    ModelResponse,
    Role,
    ToolCall,
    ToolCategory,
    ToolDefinition,
)


class RepositoryLookupTool(BaseTool):
    name = "repository_lookup"
    description = "Read repository evidence"
    category = ToolCategory.READ
    required_permission = "repository:read"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        return f"evidence for {kwargs['query']} token=secret-value-123456"


class ScriptedProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = responses
        self.calls: list[Sequence[Message]] = []

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        assert tools
        self.calls.append(messages)
        return self._responses.pop(0)


def make_context(*, permissions: set[str] | None = None) -> RunContext:
    return RunContext(
        run_id=uuid4(),
        trace_id=uuid4(),
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        user_id="u1",
        permissions=frozenset(
            permissions or {"project:payment:read", "repository:read"}
        ),
    )


@pytest.mark.asyncio
async def test_loop_executes_tool_and_emits_events() -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall("call-1", "repository_lookup", {"query": "coupon error"}),
                ),
                usage={"total_tokens": 10},
            ),
            ModelResponse(content="The coupon guard changed.", usage={"total_tokens": 5}),
        ]
    )
    registry = ToolRegistry()
    registry.register(RepositoryLookupTool())
    events = InMemoryEventSink()
    loop = AgentLoop(provider, registry, event_sink=events)

    result = await loop.run("Why did payment fail?", make_context())

    assert result.finish_reason == FinishReason.STOP
    assert result.final_text == "The coupon guard changed."
    assert result.total_tokens == 15
    tool_message = next(message for message in result.messages if message.role == Role.TOOL)
    assert "secret-value" not in (tool_message.content or "")
    assert events.events[0].type == AgentEventType.RUN_STARTED
    assert events.events[-1].type == AgentEventType.RUN_COMPLETED


@pytest.mark.asyncio
async def test_loop_returns_permission_denial_to_model() -> None:
    provider = ScriptedProvider(
        [
            ModelResponse(
                tool_calls=(ToolCall("call-1", "repository_lookup", {"query": "x"}),)
            ),
            ModelResponse(content="I cannot access that repository."),
        ]
    )
    registry = ToolRegistry()
    registry.register(RepositoryLookupTool())
    loop = AgentLoop(provider, registry)

    result = await loop.run("Check it", make_context(permissions={"repository:read"}))

    denied = next(message for message in result.messages if message.role == Role.TOOL)
    assert "missing permission: project:payment:read" in (denied.content or "")


@pytest.mark.asyncio
async def test_loop_stops_repeated_tool_batches() -> None:
    repeated = ModelResponse(
        tool_calls=(ToolCall("same", "repository_lookup", {"query": "x"}),)
    )
    provider = ScriptedProvider([repeated, repeated, repeated])
    registry = ToolRegistry()
    registry.register(RepositoryLookupTool())
    loop = AgentLoop(provider, registry, max_repeated_tool_batches=2)

    result = await loop.run("Check it", make_context())

    assert result.finish_reason == FinishReason.STALLED
    assert result.iterations == 3

