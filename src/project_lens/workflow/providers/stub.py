"""Default deterministic ModelProvider. Never calls a real LLM."""

from __future__ import annotations

import asyncio

from project_lens.workflow.context_prompt import ContextPrompt
from project_lens.workflow.providers.base import (
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    ProviderUsage,
    assert_prompt_safe,
    estimate_prompt_tokens,
)


class StubProvider:
    """Default harness provider. Answers remain on analyze/verify/compose."""

    name = "stub.v1"
    kind = ProviderKind.STUB

    def __init__(self, *, simulate_timeouts: int = 0, model_name: str = "stub-v1") -> None:
        self._simulate_timeouts = max(0, int(simulate_timeouts))
        self._model_name = model_name

    async def complete(self, prompt: ContextPrompt) -> ProviderResult:
        assert_prompt_safe(prompt)
        if self._simulate_timeouts > 0:
            self._simulate_timeouts -= 1
            await asyncio.sleep(3600)
        prompt_tokens = estimate_prompt_tokens(prompt)
        completion_tokens = 8
        return ProviderResult(
            provider=self.name,
            kind=self.kind,
            status=ProviderStatus.OK,
            content=(
                f"stub accepted ContextPrompt model={self._model_name}; "
                "deterministic analysis/composer remain the answer path"
            ),
            usage=ProviderUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            allow_apply=False,
            live=False,
            notes=(
                "stub provider completed without external LLM",
                f"model_name={self._model_name}",
            ),
        )
