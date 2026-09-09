"""Configuration for the ProjectLens Hermes plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any


@dataclass(frozen=True)
class ProjectLensPluginConfig:
    api_base_url: str = "http://127.0.0.1:8000/api/v1"
    default_tenant_id: str = "demo"
    default_project_id: str = "projectlens"
    default_user_id: str = "hermes-user"
    timeout_seconds: float = 30.0
    feishu_bindings: dict[str, dict[str, str]] = field(default_factory=dict)
    feishu_cards_enabled: bool = False
    advanced_tools_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_base_url: str = "https://open.feishu.cn"

    @classmethod
    def from_env(cls) -> "ProjectLensPluginConfig":
        timeout_raw = os.getenv("PROJECTLENS_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(timeout_raw)
        except ValueError:
            timeout = 30.0
        return cls(
            api_base_url=os.getenv("PROJECTLENS_API_BASE_URL", cls.api_base_url).rstrip("/"),
            default_tenant_id=os.getenv(
                "PROJECTLENS_DEFAULT_TENANT_ID",
                cls.default_tenant_id,
            ),
            default_project_id=os.getenv(
                "PROJECTLENS_DEFAULT_PROJECT_ID",
                cls.default_project_id,
            ),
            default_user_id=os.getenv("PROJECTLENS_DEFAULT_USER_ID", cls.default_user_id),
            timeout_seconds=timeout,
            feishu_bindings=_bindings_from_env(),
            feishu_cards_enabled=_env_flag("PROJECTLENS_FEISHU_CARDS"),
            advanced_tools_enabled=_env_flag("PROJECTLENS_HERMES_ADVANCED_TOOLS"),
            feishu_app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            feishu_base_url=os.getenv("FEISHU_BASE_URL", cls.feishu_base_url).rstrip("/"),
        )

    def project_for_chat(self, chat_id: str | None) -> dict[str, str]:
        if chat_id:
            binding = self.feishu_bindings.get(chat_id)
            if binding:
                return _project_payload(binding, self.default_tenant_id, self.default_project_id)
        return {
            "tenant_id": self.default_tenant_id,
            "project_id": self.default_project_id,
        }


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _bindings_from_env() -> dict[str, dict[str, str]]:
    raw = os.getenv("PROJECTLENS_FEISHU_BINDINGS_JSON", "").strip()
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    bindings: dict[str, dict[str, str]] = {}
    for chat_id, value in decoded.items():
        if not isinstance(chat_id, str) or not isinstance(value, dict):
            continue
        payload = _project_payload(value, "", "")
        if payload["tenant_id"] and payload["project_id"]:
            bindings[chat_id] = payload
    return bindings


def _project_payload(value: dict[str, Any], default_tenant: str, default_project: str) -> dict[str, str]:
    tenant_id = str(value.get("tenant_id") or default_tenant)
    project_id = str(value.get("project_id") or default_project)
    payload = {
        "tenant_id": tenant_id,
        "project_id": project_id,
    }
    service = value.get("service")
    environment = value.get("environment")
    if service:
        payload["service"] = str(service)
    if environment:
        payload["environment"] = str(environment)
    return payload
