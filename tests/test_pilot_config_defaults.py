"""Safe pilot defaults must keep LLM off and persistence on."""

from project_lens.config import Settings


def test_settings_safe_pilot_defaults() -> None:
    # Construct without reading a developer .env so defaults stay inspectable.
    settings = Settings(
        _env_file=None,
        model_provider="stub",
        model_live=False,
        model_fallback_to_stub=True,
        conversation_store="sqlite",
        database_path="project_lens.db",
    )
    assert settings.model_provider == "stub"
    assert settings.model_live is False
    assert settings.model_fallback_to_stub is True
    assert settings.conversation_store == "sqlite"
    assert settings.database_path != ":memory:"
    assert settings.feishu_doc_sync_on_startup is False
