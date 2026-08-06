"""AgentLoop ModelProvider that calls OpenAI-compatible chat with tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from project_lens.runtime.types import (
    Message,
    ModelResponse,
    Role,
    ToolCall,
    ToolDefinition,
)
from project_lens.workflow.providers.transport import ChatTransport, UrllibChatTransport


class OpenAILoopProvider:
    """Live (or injected-transport) chat provider for ProjectInvestigationAgent."""

    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str | None = None,
        transport: ChatTransport | None = None,
        timeout_seconds: float = 30.0,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._transport = transport or UrllibChatTransport()
        self._timeout_seconds = timeout_seconds
        self._max_tokens = max_tokens
        self._temperature = temperature
        self.last_live_effective = False

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self._model_name,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "messages": [_message_to_openai(item) for item in messages],
        }
        if tools:
            body["tools"] = [item.to_openai_schema() for item in tools]
            body["tool_choice"] = "auto"
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await asyncio.to_thread(
                self._transport.post_json,
                url,
                headers=headers,
                body=body,
                timeout_seconds=self._timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - convert to typed provider failure
            raise InvestigationProviderError(
                f"openai loop chat failed: {exc}"
            ) from exc
        self.last_live_effective = True
        return _model_response_from_openai(response)


class InvestigationProviderError(RuntimeError):
    """Raised when the live investigation chat provider fails."""


class ScriptedTransport:
    """Returns successive scripted chat.completion payloads for tests."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "url": url,
                "headers": {
                    key: ("***" if key.lower() == "authorization" else value)
                    for key, value in headers.items()
                },
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self._responses:
            raise RuntimeError("ScriptedTransport exhausted")
        return self._responses.pop(0)


def _message_to_openai(message: Message) -> dict[str, Any]:
    if message.role == Role.TOOL:
        payload: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": message.tool_call_id or "",
            "content": message.content or "",
        }
        if message.name:
            payload["name"] = message.name
        return payload
    if message.role == Role.ASSISTANT and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ],
        }
    return {
        "role": message.role.value,
        "content": message.content or "",
    }


def _model_response_from_openai(response: dict[str, Any]) -> ModelResponse:
    usage_raw = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    usage = {
        "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
        "total_tokens": int(usage_raw.get("total_tokens") or 0),
    }
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ModelResponse(content=None, usage=usage)
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ModelResponse(content=None, usage=usage)
    tool_calls_raw = message.get("tool_calls") or []
    tool_calls: list[ToolCall] = []
    if isinstance(tool_calls_raw, list):
        for item in tool_calls_raw:
            if not isinstance(item, dict):
                continue
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            name = str(function.get("name") or "")
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(item.get("id") or f"call-{uuid4().hex[:8]}"),
                    name=name,
                    arguments=arguments,
                )
            )
    content = message.get("content")
    return ModelResponse(
        content=str(content) if content is not None else None,
        tool_calls=tuple(tool_calls),
        usage=usage,
    )
