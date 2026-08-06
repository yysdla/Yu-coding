"""Provider-neutral service Agent Loop extracted from the coding agent runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from project_lens.runtime.events import (
    AgentEvent,
    AgentEventType,
    EventSink,
    NullEventSink,
)
from project_lens.runtime.security import PermissionPolicy, RegexSanitizer, RunContext
from project_lens.runtime.tools import ToolRegistry
from project_lens.runtime.types import (
    FinishReason,
    Message,
    ModelProvider,
    Role,
    RuntimeResult,
    ToolCall,
    ToolCategory,
    ToolResult,
)


class AgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        *,
        permission_policy: PermissionPolicy | None = None,
        event_sink: EventSink | None = None,
        sanitizer: RegexSanitizer | None = None,
        system_prompt: str = "You are an evidence-grounded project collaboration agent.",
        max_iterations: int = 20,
        max_total_tokens: int = 100_000,
        max_repeated_tool_batches: int = 3,
        max_runtime_seconds: float = 120.0,
    ) -> None:
        self._provider = provider
        self._tools = tools
        self._permissions = permission_policy or PermissionPolicy()
        self._events = event_sink or NullEventSink()
        self._sanitizer = sanitizer or RegexSanitizer()
        self._system_prompt = system_prompt
        self._max_iterations = max(1, max_iterations)
        self._max_total_tokens = max(1, max_total_tokens)
        self._max_repeated_tool_batches = max(1, max_repeated_tool_batches)
        self._max_runtime_seconds = max(0.1, max_runtime_seconds)

    async def run(
        self,
        question: str,
        context: RunContext,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> RuntimeResult:
        cancellation = cancel_event or asyncio.Event()
        messages = [
            Message(Role.SYSTEM, self._system_prompt),
            Message(Role.USER, question),
        ]
        await self._emit(context, AgentEventType.RUN_STARTED)
        try:
            async with asyncio.timeout(self._max_runtime_seconds):
                return await self._run_loop(messages, context, cancellation)
        except TimeoutError:
            await self._emit(
                context,
                AgentEventType.RUN_FAILED,
                {"reason": "runtime limit exceeded"},
            )
            return RuntimeResult(
                tuple(messages),
                "Stopped: runtime limit exceeded.",
                FinishReason.ERROR,
                0,
                0,
            )

    async def _run_loop(
        self,
        messages: list[Message],
        context: RunContext,
        cancellation: asyncio.Event,
    ) -> RuntimeResult:
        total_tokens = 0
        previous_batch: str | None = None
        repeated_batches = 0

        for iteration in range(1, self._max_iterations + 1):
            if cancellation.is_set():
                return await self._finish(
                    messages, context, None, FinishReason.CANCELED, iteration - 1, total_tokens
                )
            await self._emit(
                context,
                AgentEventType.MODEL_REQUESTED,
                {"iteration": iteration},
            )
            response = await self._provider.chat(tuple(messages), self._tools.definitions())
            total_tokens += int(response.usage.get("total_tokens", 0))
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )
            await self._emit(
                context,
                AgentEventType.MODEL_RESPONDED,
                {
                    "iteration": iteration,
                    "tool_count": len(response.tool_calls),
                    "total_tokens": total_tokens,
                },
            )
            if total_tokens >= self._max_total_tokens:
                return await self._finish(
                    messages,
                    context,
                    response.content,
                    FinishReason.MAX_TOKENS,
                    iteration,
                    total_tokens,
                )
            if not response.tool_calls:
                return await self._finish(
                    messages,
                    context,
                    response.content,
                    FinishReason.STOP,
                    iteration,
                    total_tokens,
                )

            signature = _batch_signature(response.tool_calls)
            if signature == previous_batch:
                repeated_batches += 1
            else:
                previous_batch = signature
                repeated_batches = 1
            if repeated_batches > self._max_repeated_tool_batches:
                return await self._finish(
                    messages,
                    context,
                    "Stopped: repeated identical tool calls.",
                    FinishReason.STALLED,
                    iteration,
                    total_tokens,
                )

            tool_results = await self._execute_tools(response.tool_calls, context)
            for result in tool_results:
                messages.append(
                    Message(
                        role=Role.TOOL,
                        content=result.content,
                        tool_call_id=result.tool_call_id,
                        name=result.name,
                    )
                )

        return await self._finish(
            messages,
            context,
            None,
            FinishReason.MAX_ITERATIONS,
            self._max_iterations,
            total_tokens,
        )

    async def _execute_tools(
        self,
        calls: Sequence[ToolCall],
        context: RunContext,
    ) -> list[ToolResult]:
        approved: list[ToolCall] = []
        denied: list[ToolResult] = []
        for call in calls:
            definition = self._tools.definition(call.name)
            if not definition:
                denied.append(ToolResult(call.id, call.name, f"unknown tool: {call.name}", True))
                continue
            decision = self._permissions.check(definition, context)
            if not decision.allowed:
                denied.append(ToolResult(call.id, call.name, decision.reason, True))
                await self._emit(
                    context,
                    AgentEventType.TOOL_DENIED,
                    {"tool_call_id": call.id, "tool": call.name, "reason": decision.reason},
                )
                continue
            approved.append(call)

        parallel = len(approved) > 1 and all(
            self._tools.definition(call.name).category == ToolCategory.READ  # type: ignore[union-attr]
            for call in approved
        )
        if parallel:
            executed = await asyncio.gather(
                *(self._execute_one(call, context) for call in approved)
            )
        else:
            executed = [await self._execute_one(call, context) for call in approved]
        by_id = {result.tool_call_id: result for result in [*denied, *executed]}
        return [by_id[call.id] for call in calls]

    async def _execute_one(self, call: ToolCall, context: RunContext) -> ToolResult:
        await self._emit(
            context,
            AgentEventType.TOOL_STARTED,
            {"tool_call_id": call.id, "tool": call.name},
        )
        result = await self._tools.execute(call.id, call.name, call.arguments)
        safe_result = ToolResult(
            result.tool_call_id,
            result.name,
            self._sanitizer.sanitize(result.content),
            result.is_error,
        )
        await self._emit(
            context,
            AgentEventType.TOOL_COMPLETED,
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "is_error": safe_result.is_error,
            },
        )
        return safe_result

    async def _finish(
        self,
        messages: list[Message],
        context: RunContext,
        final_text: str | None,
        reason: FinishReason,
        iterations: int,
        total_tokens: int,
    ) -> RuntimeResult:
        event_type = (
            AgentEventType.RUN_COMPLETED
            if reason in {FinishReason.STOP, FinishReason.MAX_TOKENS}
            else AgentEventType.RUN_FAILED
        )
        await self._emit(
            context,
            event_type,
            {
                "finish_reason": reason.value,
                "iterations": iterations,
                "total_tokens": total_tokens,
            },
        )
        return RuntimeResult(tuple(messages), final_text, reason, iterations, total_tokens)

    async def _emit(
        self,
        context: RunContext,
        event_type: AgentEventType,
        payload: dict | None = None,
    ) -> None:
        await self._events.emit(
            AgentEvent(
                run_id=context.run_id,
                trace_id=context.trace_id,
                type=event_type,
                payload=payload or {},
            )
        )


def _batch_signature(calls: Sequence[ToolCall]) -> str:
    serializable = [
        {"name": call.name, "arguments": call.arguments}
        for call in calls
    ]
    return json.dumps(serializable, sort_keys=True, ensure_ascii=False, default=repr)

