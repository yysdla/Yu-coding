"""Feishu callback request models."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FeishuCallbackHeader(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    tenant_key: str = Field(min_length=1)


class FeishuSenderId(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str | None = None
    open_id: str | None = None


class FeishuSender(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sender_id: FeishuSenderId


class FeishuMessageBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    chat_type: str | None = None
    message_type: str = "text"
    content: str = Field(default="")

    def text(self) -> str:
        try:
            parsed: dict[str, Any] = json.loads(self.content)
        except json.JSONDecodeError:
            raw = self.content.strip()
        else:
            raw = str(parsed.get("text", "")).strip()
        # Feishu group @bot often injects @_user_1; strip before routing.
        from project_lens.integrations.feishu.intent import strip_feishu_mentions

        return strip_feishu_mentions(raw)


class FeishuMessageEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sender: FeishuSender
    message: FeishuMessageBody


class FeishuEventCallback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_: str | None = Field(default=None, alias="schema")
    token: str | None = None
    challenge: str | None = None
    type: str | None = None
    header: FeishuCallbackHeader | None = None
    event: FeishuMessageEvent | None = None

    @property
    def is_url_verification(self) -> bool:
        return self.type == "url_verification" and bool(self.challenge)
