"""Tool contract, registry, and JSON-schema argument validation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from project_lens.runtime.types import ToolCategory, ToolDefinition, ToolResult


class ToolArgumentValidationError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = issues
        super().__init__("; ".join(issues))


class BaseTool(ABC):
    name: str
    description: str
    category: ToolCategory = ToolCategory.READ
    required_permission: str

    @abstractmethod
    def parameters_schema(self) -> dict[str, Any]: ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str: ...

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters_schema(),
            category=self.category,
            required_permission=self.required_permission,
        )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition() for tool in self._tools.values())

    def definition(self, name: str) -> ToolDefinition | None:
        tool = self._tools.get(name)
        return tool.definition() if tool else None

    async def execute(
        self,
        tool_call_id: str,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(tool_call_id, name, f"unknown tool: {name}", is_error=True)
        try:
            validate_arguments(tool.parameters_schema(), arguments)
        except ToolArgumentValidationError as exc:
            detail = json.dumps({"error": "invalid_arguments", "issues": exc.issues})
            return ToolResult(tool_call_id, name, detail, is_error=True)
        try:
            content = await tool.execute(**arguments)
            return ToolResult(tool_call_id, name, str(content))
        except Exception as exc:
            return ToolResult(tool_call_id, name, f"tool failed: {exc}", is_error=True)


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ToolArgumentValidationError(["arguments must be an object"])
    issues: list[str] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        if key not in arguments:
            issues.append(f"missing required argument: {key}")
    if schema.get("additionalProperties", False) is False:
        for key in arguments:
            if key not in properties:
                issues.append(f"unknown argument: {key}")
    for key, value in arguments.items():
        item_schema = properties.get(key)
        if not isinstance(item_schema, dict):
            continue
        expected = item_schema.get("type")
        if expected and not _matches_type(expected, value):
            issues.append(f"{key} expected {expected}, got {type(value).__name__}")
        enum = item_schema.get("enum")
        if enum and value not in enum:
            issues.append(f"{key} must be one of {enum}")
    if issues:
        raise ToolArgumentValidationError(issues)


def _matches_type(expected: str, value: Any) -> bool:
    types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    python_type = types.get(expected)
    if python_type is None:
        return True
    if expected in {"integer", "number"} and isinstance(value, bool):
        return False
    return isinstance(value, python_type)

