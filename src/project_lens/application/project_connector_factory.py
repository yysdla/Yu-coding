"""Build read-only connectors from ProjectSpace-owned configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from project_lens.context.connectors import (
    Connector,
    FeishuBitableConnector,
    FeishuDocumentConnector,
    FeishuMinutesConnector,
    FeishuProjectConnector,
    GitHubConnector,
)
from project_lens.context.connectors.feishu_bitable import FeishuBitableReader
from project_lens.context.connectors.github import GitHubReadClient
from project_lens.project_space.models import ProjectSpace, SourceConnectorRef
from project_lens.project_space.registry import ProjectRegistry


class ProjectConnectorFactory:
    """Only the application layer may turn a ProjectSpace ref into a connector."""

    def __init__(
        self,
        *,
        project_registry: ProjectRegistry,
        feishu_token_provider: Any | None = None,
        feishu_base_url: str = "https://open.feishu.cn",
        github_token: str | None = None,
        github_api_base_url: str = "https://api.github.com",
    ) -> None:
        self._registry = project_registry
        self._feishu_token_provider = feishu_token_provider
        self._feishu_base_url = feishu_base_url
        self._github_token = github_token
        self._github_api_base_url = github_api_base_url

    def build(
        self,
        *,
        tenant_id: str,
        project_id: str,
        names: tuple[str, ...] = (),
    ) -> tuple[Connector, ...]:
        space = self._registry.require(tenant_id, project_id)
        wanted = frozenset(names)
        connectors: list[Connector] = []
        for ref in space.source_connectors:
            if wanted and ref.name not in wanted and ref.kind not in wanted:
                continue
            connector = self._build_ref(space, ref)
            if connector is not None:
                connectors.append(connector)
        return tuple(connectors)

    def _build_ref(self, space: ProjectSpace, ref: SourceConnectorRef) -> Connector | None:
        kind = ref.kind.strip().lower()
        metadata = dict(ref.metadata or {})
        records = _load_records(ref.path or _metadata_path(metadata))
        if kind in {"feishu_bitable", "bitable"}:
            app_token = _required_str(metadata, "app_token")
            table_id = _required_str(metadata, "table_id")
            if app_token and table_id:
                if self._feishu_token_provider is None:
                    raise ValueError(f"connector {ref.name} requires Feishu credentials")
                reader = FeishuBitableReader(
                    token_provider=self._feishu_token_provider,
                    app_token=app_token,
                    table_id=table_id,
                    base_url=self._feishu_base_url,
                    field_aliases=_string_mapping(metadata.get("field_aliases")),
                )
                return FeishuBitableConnector(
                    space.project,
                    reader=reader,
                    topic=str(metadata.get("topic") or "requirement"),
                    default_status=str(metadata.get("default_status") or "published"),
                    authority_scope=tuple(str(item) for item in metadata["authority_scope"])
                    if isinstance(metadata.get("authority_scope"), (list, tuple))
                    else None,
                )
            return FeishuBitableConnector(
                space.project,
                records=records,
                topic=str(metadata.get("topic") or "requirement"),
                default_status=str(metadata.get("default_status") or "published"),
                authority_scope=tuple(str(item) for item in metadata["authority_scope"])
                if isinstance(metadata.get("authority_scope"), (list, tuple))
                else None,
            )
        if kind in {"github", "github_repository"}:
            repository = _required_str(metadata, "repository")
            if repository:
                reader = GitHubReadClient(
                    repository=repository,
                    token=self._github_token,
                    base_url=self._github_api_base_url,
                )
                return GitHubConnector(space.project, reader=reader)
            return GitHubConnector(space.project, records=records)
        if kind in {"meeting_minutes", "feishu_minutes", "minutes"}:
            return FeishuMinutesConnector(space.project, records=records)
        if kind in {"feishu_project", "project_tasks"}:
            return FeishuProjectConnector(space.project, records=records)
        if kind in {"feishu_documents_fixture", "feishu_documents_file"}:
            return FeishuDocumentConnector(space.project, records=records)
        # `feishu_docs` is served by FeishuDocumentSyncService because it reads
        # docx content through a dedicated revision-aware client.
        if kind in {"feishu_docs", "repo", "documents"}:
            return None
        raise ValueError(f"unsupported source connector kind: {ref.kind}")


def _load_records(path: Path | None) -> tuple[dict[str, Any], ...]:
    if path is None:
        return ()
    if not path.is_file():
        raise ValueError(f"connector fixture source does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("records", ()) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise ValueError(f"connector fixture must contain a JSON array of records: {path}")
    return tuple(dict(item) for item in rows)


def _metadata_path(metadata: dict[str, Any]) -> Path | None:
    raw = metadata.get("records_path")
    return Path(str(raw)) if raw else None


def _required_str(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    text = str(value).strip() if value is not None else ""
    return text or None


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


__all__ = ["ProjectConnectorFactory"]
