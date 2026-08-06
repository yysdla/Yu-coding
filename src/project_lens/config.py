"""Application configuration."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PROJECT_LENS_",
        extra="ignore",
    )

    env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    database_path: str = "project_lens.db"
    # ConversationSession backend: "sqlite" (default) or "memory" fallback.
    conversation_store: str = "sqlite"
    # ContextPrompt ModelProvider: stub (default) | openai | internal | local.
    # Live network / local inference stays disabled in the harness cut.
    model_provider: str = "stub"
    model_name: str = "stub-v1"
    model_timeout_seconds: float = 30.0
    model_max_retries: int = 1
    model_max_tokens: int = 2048
    model_temperature: float = 0.0
    # Safe live LLM (Phase 3): default off; requires api_key + provider support.
    model_live: bool = False
    model_fallback_to_stub: bool = True
    model_openai_base_url: str | None = None
    model_openai_model: str | None = None
    model_openai_api_key: str | None = None
    model_internal_endpoint: str | None = None
    model_local_path: str | None = None
    feishu_verification_token: str = "project-lens-local-token"
    feishu_signing_secret: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_api_base_url: str = "https://open.feishu.cn"
    feishu_project_bindings: str = ""
    local_project_registry: str = ""
    # Show Skill / provider / usage / trace at card bottom as debug (not first screen).
    feishu_show_debug_audit: bool = True
    # Document sync ops: off by default so local tests and chat paths stay isolated.
    feishu_doc_sync_on_startup: bool = False
    feishu_doc_sync_interval_seconds: int = 0
    # Project question execution path: workflow (legacy) | read_agent (investigation loop).
    agent_mode: str = "workflow"
    # Feishu natural project questions can optionally use the Hermes LLM tool loop bridge.
    feishu_use_hermes_tool_loop: bool = False
    # Local Hermes agent checkout (sys.path) for in-process AIAgent. Empty = rely on PYTHONPATH.
    hermes_repo: str = ""
    feishu_hermes_provider: str = "deepseek"
    feishu_hermes_model: str = "deepseek-v4-flash"
    # Optional directory of ProjectSpace JSON files (relative to repo root).
    project_spaces_dir: str = "config/projects"


settings = Settings()
