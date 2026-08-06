"""Isolated settings helpers for investigation / read_agent tests.

Avoids pollution from the developer's local ``.env`` (live OpenAI keys, etc.).
Uses a dedicated env prefix so ``PROJECT_LENS_*`` process env cannot leak in.
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from project_lens.config import Settings


class IsolatedInvestigationSettings(Settings):
    """Constructor kwargs win; ignore repo ``.env`` and ``PROJECT_LENS_*`` env."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_prefix="PROJECT_LENS_TEST_ISOLATED_",
        extra="ignore",
    )


def stub_investigation_settings(**overrides: object) -> IsolatedInvestigationSettings:
    """Explicit stub settings for read_agent unit tests."""

    payload = {
        "model_provider": "stub",
        "model_live": False,
        "model_fallback_to_stub": True,
        "model_openai_api_key": None,
        "agent_mode": "read_agent",
        **overrides,
    }
    return IsolatedInvestigationSettings(**payload)  # type: ignore[arg-type]
