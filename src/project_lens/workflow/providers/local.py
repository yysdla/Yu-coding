"""Reserved local/on-device model provider. Live inference disabled."""

from __future__ import annotations

from project_lens.workflow.context_prompt import ContextPrompt
from project_lens.workflow.providers.base import (
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    ProviderUsage,
    assert_live_disabled,
    assert_prompt_safe,
    estimate_prompt_tokens,
)


class LocalProvider:
    name = "local.reserved"
    kind = ProviderKind.LOCAL

    def __init__(
        self,
        *,
        model_name: str = "local-reserved",
        model_path: str | None = None,
        live: bool = False,
    ) -> None:
        self._model_name = model_name
        self._model_path = model_path
        self._live = live

    async def complete(self, prompt: ContextPrompt) -> ProviderResult:
        assert_prompt_safe(prompt)
        assert_live_disabled(live=self._live, provider_name=self.name)
        prompt_tokens = estimate_prompt_tokens(prompt)
        return ProviderResult(
            provider=self.name,
            kind=self.kind,
            status=ProviderStatus.RESERVED,
            content=None,
            usage=ProviderUsage(prompt_tokens=prompt_tokens, total_tokens=prompt_tokens),
            allow_apply=False,
            live=False,
            notes=(
                "Local provider reserved; no model weights loaded",
                f"model_name={self._model_name}",
                f"model_path_configured={bool(self._model_path)}",
            ),
        )
