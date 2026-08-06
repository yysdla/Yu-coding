from typing import Any

import pytest

from project_lens.runtime.tools import BaseTool, ToolRegistry
from project_lens.runtime.types import ToolCategory


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo text"
    category = ToolCategory.READ
    required_permission = "tool:echo:read"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }

    async def execute(self, **kwargs: Any) -> str:
        return kwargs["text"]


@pytest.mark.asyncio
async def test_tool_registry_validates_arguments() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await registry.execute("call-1", "echo", {"wrong": "value"})

    assert result.is_error
    assert "missing required argument" in result.content
    assert "unknown argument" in result.content


def test_tool_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())

