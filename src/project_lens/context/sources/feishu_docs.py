"""Feishu document source records used by the document indexer."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from project_lens.domain.models import ProjectRef

if TYPE_CHECKING:
    from project_lens.integrations.feishu.docs_client import FeishuDocRaw


class FeishuDocumentRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=100)
    project_id: str = Field(min_length=1, max_length=100)
    doc_token: str = Field(min_length=1, max_length=200)
    doc_url: str = Field(min_length=1, max_length=1_000)
    title: str = Field(default="", max_length=300)
    content: str = ""
    revision: str = Field(min_length=1, max_length=100)
    updated_at: datetime
    owner_user_id: str | None = Field(default=None, max_length=100)
    access_scope: str = Field(min_length=1, max_length=200)


class FeishuDocumentSource:
    """Normalizes Feishu document payloads before indexing."""

    def normalize(self, record: FeishuDocumentRecord) -> FeishuDocumentRecord:
        content = record.content.strip()
        if not content:
            raise ValueError("feishu document content is empty")
        if not record.access_scope.strip():
            raise ValueError("feishu document access_scope is required")
        return record


def to_feishu_document_record(
    raw: "FeishuDocRaw",
    *,
    project: ProjectRef,
    access_scope: str,
) -> FeishuDocumentRecord:
    """Map OpenAPI raw document data into the existing indexer contract."""

    return FeishuDocumentRecord(
        tenant_id=project.tenant_id,
        project_id=project.project_id,
        doc_token=raw.doc_token,
        doc_url=raw.doc_url or f"https://feishu.cn/docx/{raw.doc_token}",
        title=raw.title,
        content=raw.content,
        revision=raw.revision,
        updated_at=raw.updated_at,
        owner_user_id=raw.owner_user_id,
        access_scope=access_scope,
    )
