"""Feishu Docx OpenAPI client for read-only document sync."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from project_lens.integrations.feishu.http_adapter import (
    FeishuHttpTransport,
    FeishuTenantTokenProvider,
    UrllibFeishuTransport,
)


@dataclass(frozen=True)
class FeishuDocRaw:
    """Normalized raw payload fetched from Feishu OpenAPI."""

    doc_token: str
    title: str
    content: str
    revision: str
    updated_at: datetime
    owner_user_id: str | None = None
    doc_url: str | None = None


class FeishuDocClient:
    """Fetch docx metadata and plain text using tenant_access_token."""

    def __init__(
        self,
        *,
        token_provider: FeishuTenantTokenProvider,
        base_url: str = "https://open.feishu.cn",
        transport: FeishuHttpTransport | None = None,
        max_retries: int = 2,
    ) -> None:
        self._token_provider = token_provider
        self._base_url = base_url.rstrip("/")
        self._transport = transport or UrllibFeishuTransport()
        self._max_retries = max_retries

    def get_document(self, doc_token: str) -> FeishuDocRaw:
        meta = self._get_json(f"/open-apis/docx/v1/documents/{quote(doc_token, safe='')}")
        document = (meta.get("data") or {}).get("document") or {}
        raw_content = self._get_json(
            f"/open-apis/docx/v1/documents/{quote(doc_token, safe='')}/raw_content"
        )
        content = str(((raw_content.get("data") or {}).get("content")) or "")
        revision_id = document.get("revision_id")
        revision = str(revision_id if revision_id is not None else document.get("revision") or "0")
        title = str(document.get("title") or "")
        updated_at = _parse_updated_at(document)
        owner_user_id = _optional_str(
            document.get("owner_id")
            or document.get("owner_user_id")
            or ((meta.get("data") or {}).get("owner_id"))
        )
        return FeishuDocRaw(
            doc_token=doc_token,
            title=title,
            content=content,
            revision=revision,
            updated_at=updated_at,
            owner_user_id=owner_user_id,
            doc_url=f"https://feishu.cn/docx/{doc_token}",
        )

    def _get_json(self, path: str) -> dict[str, Any]:
        last_payload: object = None
        for attempt in range(self._max_retries + 1):
            token = self._token_provider.get()
            status_code, payload = self._transport.request(
                method="GET",
                url=f"{self._base_url}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            if status_code < 400 and int(payload.get("code", 0)) == 0:
                return payload
            last_payload = payload
            if status_code not in {429, 500, 502, 503, 504} or attempt >= self._max_retries:
                break
            time.sleep(0.2 * (2**attempt))
        raise RuntimeError(f"Feishu doc request failed for {path}: {last_payload}")


def _parse_updated_at(document: dict[str, Any]) -> datetime:
    for key in ("edit_time", "update_time", "updated_at"):
        value = document.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            # Feishu often returns unix seconds.
            ts = float(value)
            if ts > 1_000_000_000_000:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(value, str) and value.strip():
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
