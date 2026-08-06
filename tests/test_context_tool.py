import json
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest

from project_lens.context.tools import SearchProjectContextTool
from project_lens.runtime.security import RunContext
from project_lens.runtime.tools import ToolRegistry
from project_lens.runtime.loop import AgentLoop
from project_lens.runtime.types import Message, ModelResponse, ToolCall, ToolDefinition
from tests.context_helpers import ACCESS_SCOPE, build_demo_engine


DEMO_ROOT = Path(__file__).parents[1] / "examples" / "payment_service"


@pytest.mark.asyncio
async def test_search_context_tool_returns_structured_bundle() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)
    context = RunContext(
        run_id=uuid4(),
        trace_id=uuid4(),
        project=project,
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE, "context:search"}),
    )
    registry = ToolRegistry()
    registry.register(SearchProjectContextTool(engine, context))

    result = await registry.execute(
        "call-1",
        "search_project_context",
        {
            "query": "coupon null guard",
            "project_id": "payment",
            "source_types": ["code", "document", "incident"],
            "limit": 5,
        },
    )

    payload = json.loads(result.content)
    assert not result.is_error
    assert payload["hits"]
    assert payload["hits"][0]["evidence"]["source"]["source_id"]


@pytest.mark.asyncio
async def test_search_context_tool_rejects_cross_project_request() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)
    context = RunContext(
        run_id=uuid4(),
        trace_id=uuid4(),
        project=project,
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE, "context:search"}),
    )
    registry = ToolRegistry()
    registry.register(SearchProjectContextTool(engine, context))

    result = await registry.execute(
        "call-1",
        "search_project_context",
        {"query": "status", "project_id": "other-project"},
    )

    assert result.is_error
    assert "authorized run project" in result.content


class ContextCallingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            assert any(tool.name == "search_project_context" for tool in tools)
            return ModelResponse(
                tool_calls=(
                    ToolCall(
                        "context-1",
                        "search_project_context",
                        {
                            "query": "coupon null guard",
                            "project_id": "payment",
                            "limit": 5,
                        },
                    ),
                )
            )
        assert any(message.name == "search_project_context" for message in messages)
        return ModelResponse(content="The evidence identifies missing coupon null handling.")


@pytest.mark.asyncio
async def test_agent_loop_calls_context_tool_end_to_end() -> None:
    engine, _index, project = build_demo_engine(DEMO_ROOT)
    context = RunContext(
        run_id=uuid4(),
        trace_id=uuid4(),
        project=project,
        user_id="u1",
        permissions=frozenset({ACCESS_SCOPE, "context:search"}),
    )
    registry = ToolRegistry()
    registry.register(SearchProjectContextTool(engine, context))

    result = await AgentLoop(ContextCallingProvider(), registry).run(
        "Why did orders without coupons fail?",
        context,
    )

    assert result.final_text == "The evidence identifies missing coupon null handling."
