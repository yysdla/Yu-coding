"""Explicit, opt-in Feishu Wiki publisher for reviewed ProjectLens drafts."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

from project_lens.application.wiki_compiler import WikiPageDraft, WikiPublisher
from project_lens.config import assert_external_calls_allowed
from project_lens.integrations.feishu.http_adapter import (
    FeishuHttpTransport,
    FeishuTenantTokenProvider,
    UrllibFeishuTransport,
)


class FeishuWikiPublisher(WikiPublisher):
    """Publish reviewed drafts to a configured Wiki node using a tenant token.

    The endpoint is configurable because Feishu Wiki deployments differ in
    whether they create a new node or update a pre-provisioned document node.
    This adapter never runs unless explicitly constructed by application code.
    """

    def __init__(
        self,
        *,
        token_provider: FeishuTenantTokenProvider,
        space_id: str,
        parent_node_token: str | None = None,
        base_url: str = "https://open.feishu.cn",
        endpoint_path: str = "/open-apis/wiki/v2/nodes",
        transport: FeishuHttpTransport | None = None,
        max_retries: int = 2,
    ) -> None:
        self._token_provider = token_provider
        self._space_id = space_id
        self._parent_node_token = parent_node_token or ""
        self._base_url = base_url.rstrip("/")
        self._endpoint_path = "/" + endpoint_path.lstrip("/")
        self._transport = transport or UrllibFeishuTransport()
        self._max_retries = max(0, max_retries)

    def publish(self, draft: WikiPageDraft) -> str:
        assert_external_calls_allowed("feishu_wiki")
        payload = {
            "space_id": self._space_id,
            "parent_node_token": self._parent_node_token,
            "title": draft.title,
            "obj_type": "docx",
            "node_type": "origin",
            "content": draft.content,
            "project_id": draft.project_id,
            "page_type": draft.page_type.value,
            "source_keys": list(draft.source_keys),
            "generated_at": draft.generated_at.isoformat(),
        }
        last_payload: object = None
        for attempt in range(self._max_retries + 1):
            status_code, response = self._transport.request(
                method="POST",
                url=f"{self._base_url}{self._endpoint_path}",
                headers={
                    "Authorization": f"Bearer {self._token_provider.get()}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            if status_code < 400 and int(response.get("code", 0)) == 0:
                data = response.get("data") or {}
                node = data.get("node") or data
                uri = node.get("url") or node.get("node_token") or node.get("obj_token")
                return str(uri or f"{self._base_url}{self._endpoint_path}")
            last_payload = response
            if status_code not in {429, 500, 502, 503, 504} or attempt >= self._max_retries:
                break
            time.sleep(0.2 * (2**attempt))
        raise RuntimeError(f"Feishu Wiki publish failed: {last_payload}")


__all__ = ["FeishuWikiPublisher"]
