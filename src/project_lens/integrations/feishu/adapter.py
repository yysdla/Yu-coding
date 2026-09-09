"""Feishu outbound messaging adapters."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class FeishuMessage(BaseModel):
    chat_id: str
    message_type: str
    content: dict[str, Any]


class FeishuMessenger(Protocol):
    async def post_text(self, chat_id: str, text: str) -> None: ...

    async def post_card(self, chat_id: str, card: dict[str, Any]) -> None: ...

    async def post_user_card(self, open_id: str, card: dict[str, Any]) -> None: ...


class RecordingFeishuMessenger:
    """Local adapter used by tests and demos before real Feishu API credentials exist."""

    def __init__(self) -> None:
        self.messages: list[FeishuMessage] = []

    async def post_text(self, chat_id: str, text: str) -> None:
        self.messages.append(
            FeishuMessage(
                chat_id=chat_id,
                message_type="text",
                content={"text": text},
            )
        )

    async def post_card(self, chat_id: str, card: dict[str, Any]) -> None:
        self.messages.append(
            FeishuMessage(
                chat_id=chat_id,
                message_type="interactive",
                content=card,
            )
        )

    async def post_user_card(self, open_id: str, card: dict[str, Any]) -> None:
        self.messages.append(
            FeishuMessage(
                chat_id=open_id,
                message_type="interactive:p2p",
                content=card,
            )
        )


class FeishuCard(BaseModel):
    title: str = Field(min_length=1)
    elements: list[dict[str, Any]]

    def to_payload(self) -> dict[str, Any]:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": self.title},
            },
            "elements": self.elements,
        }
