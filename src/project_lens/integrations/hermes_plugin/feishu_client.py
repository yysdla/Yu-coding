"""Thin Feishu Open API client for Hermes plugin card posting/patching.

Self-contained so the plugin process does not import ProjectLens Feishu
service wiring. Uses the same bot credentials as Hermes (FEISHU_APP_ID /
FEISHU_APP_SECRET).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FeishuTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]: ...


class UrllibTransport:
    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout = timeout_seconds

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None = None,
    ) -> tuple[int, dict[str, Any]]:
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self._timeout) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"msg": raw}
            return exc.code, payload
        except URLError as exc:
            raise RuntimeError(f"Feishu transport unavailable: {exc.reason}") from exc


@dataclass
class _TokenCache:
    value: str | None = None
    expires_at: float = 0.0


class FeishuCardClient:
    """Post and patch interactive cards via Feishu IM Open API."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        base_url: str = "https://open.feishu.cn",
        transport: FeishuTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("FeishuCardClient requires app_id and app_secret")
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._transport = transport or UrllibTransport(timeout_seconds=timeout_seconds)
        self._cache = _TokenCache()
        self._lock = Lock()

    def post_interactive_card(self, *, chat_id: str, card: dict[str, Any]) -> str:
        """Create an interactive card message. Returns Feishu message_id."""

        if not chat_id.strip():
            raise ValueError("chat_id is required to post a Feishu card")
        token = self._tenant_access_token()
        status_code, payload = self._transport.request(
            method="POST",
            url=f"{self._base_url}/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            body=json.dumps(
                {
                    "receive_id": chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        if status_code >= 400 or payload.get("code", 0) != 0:
            raise RuntimeError(f"Feishu card post failed: {payload}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        message_id = str(data.get("message_id") or "").strip()
        if not message_id:
            raise RuntimeError(f"Feishu card post missing message_id: {payload}")
        return message_id

    def patch_interactive_card(self, *, message_id: str, card: dict[str, Any]) -> None:
        """In-place update an interactive card by message_id."""

        mid = message_id.strip()
        if not mid:
            raise ValueError("message_id is required to patch a Feishu card")
        token = self._tenant_access_token()
        status_code, payload = self._transport.request(
            method="PATCH",
            url=f"{self._base_url}/open-apis/im/v1/messages/{mid}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            body=json.dumps(
                {"content": json.dumps(card, ensure_ascii=False)},
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        if status_code >= 400 or payload.get("code", 0) != 0:
            raise RuntimeError(f"Feishu card patch failed: {payload}")

    def _tenant_access_token(self) -> str:
        now = time.time()
        with self._lock:
            if self._cache.value and now < self._cache.expires_at:
                return self._cache.value
            status_code, payload = self._transport.request(
                method="POST",
                url=f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                headers={"Content-Type": "application/json; charset=utf-8"},
                body=json.dumps(
                    {"app_id": self._app_id, "app_secret": self._app_secret}
                ).encode("utf-8"),
            )
            token = payload.get("tenant_access_token")
            if status_code >= 400 or not token:
                raise RuntimeError(f"Feishu token request failed: {payload}")
            expires_in = int(payload.get("expire", 3600))
            self._cache = _TokenCache(
                value=str(token),
                expires_at=now + max(60, expires_in - 60),
            )
            return str(token)
