"""Injectable HTTP transport for live model providers (testable without network)."""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ChatTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]: ...


class UrllibChatTransport:
    """Production transport using stdlib urllib. Never used in unit tests."""

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        request = Request(url, data=payload, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"http {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"network error: {exc.reason}") from exc
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("provider response must be a JSON object")
        return data


class RecordingChatTransport:
    """Test double: records calls and returns a scripted payload."""

    def __init__(self, response: dict[str, Any] | None = None, *, error: Exception | None = None) -> None:
        self.response = response or {}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        safe_headers = {
            key: ("***" if key.lower() == "authorization" else value)
            for key, value in headers.items()
        }
        self.calls.append(
            {
                "url": url,
                "headers": safe_headers,
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response
