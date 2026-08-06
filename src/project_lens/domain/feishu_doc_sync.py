"""Feishu document sync operational status contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from project_lens.domain.models import ProjectRef, utc_now


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FeishuDocSyncStatusValue(StrEnum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class FeishuDocSyncStatus(FrozenModel):
    project: ProjectRef
    doc_token: str = Field(min_length=1, max_length=200)
    revision: str | None = Field(default=None, max_length=100)
    last_synced_at: datetime = Field(default_factory=utc_now)
    status: FeishuDocSyncStatusValue
    error: str | None = Field(default=None, max_length=2_000)
    title: str | None = Field(default=None, max_length=300)
    owner_user_id: str | None = Field(default=None, max_length=100)
    doc_url: str | None = Field(default=None, max_length=500)
    access_scope: str | None = Field(default=None, max_length=200)
    # Last successfully synced / skipped revision; preserved across FAILED attempts.
    last_success_revision: str | None = Field(default=None, max_length=100)
