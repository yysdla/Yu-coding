"""Immutable normalized source snapshots shared by connectors and retrieval."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from project_lens.domain.models import Evidence, EvidenceType, FrozenModel, ProjectRef, SourceRef


class SourceType(StrEnum):
    FEISHU_DOCUMENT = "feishu_document"
    FEISHU_BITABLE = "feishu_bitable"
    FEISHU_PROJECT = "feishu_project"
    MEETING_MINUTE = "meeting_minute"
    GITHUB_ISSUE = "github_issue"
    GITHUB_PULL_REQUEST = "github_pull_request"
    GITHUB_COMMIT = "github_commit"
    GITHUB_CODE = "github_code"
    OTHER = "other"


class FactType(StrEnum):
    REQUIREMENT_SCOPE = "requirement_scope"
    REQUIREMENT_STATUS = "requirement_status"
    DEVELOPMENT_PROGRESS = "development_progress"
    TEST_STATUS = "test_status"
    TECHNICAL_DECISION = "technical_decision"
    OWNER = "owner"


class SourceRecord(FrozenModel):
    """A versioned source snapshot. Raw sources are never replaced by this model."""

    source_id: str = Field(min_length=1, max_length=500)
    source_type: SourceType = SourceType.OTHER
    project_id: str = Field(min_length=1, max_length=100)
    tenant_id: str = Field(default="", max_length=100)
    title: str = Field(default="", max_length=500)
    raw_uri: str = Field(default="", max_length=1_000)
    revision: str = Field(min_length=1, max_length=200)
    observed_at: datetime
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    owner: str | None = Field(default=None, max_length=200)
    status: str = Field(default="observed", max_length=80)
    topic: str = Field(default="", max_length=200)
    authority_scope: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    related_sources: tuple[str, ...] = ()
    access_scope: str = Field(min_length=1, max_length=200)
    content_hash: str = Field(min_length=16, max_length=128)
    raw_content_ref: str = Field(default="", max_length=1_000)
    content: str = Field(min_length=1)
    fact_values: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    revoked: bool = False

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.tenant_id, self.project_id, self.source_id, self.revision)

    @classmethod
    def from_evidence(cls, item: Evidence) -> "SourceRecord":
        metadata = dict(item.metadata)
        revision = str(metadata.get("revision") or metadata.get("version") or item.content_hash)
        source_type = _source_type(item)
        authority = metadata.get("authority_scope") or metadata.get("authority") or _default_authority(source_type)
        if isinstance(authority, str):
            authority = (authority,)
        elif not isinstance(authority, (list, tuple, set)):
            authority = ()
        related = metadata.get("related_sources") or ()
        if isinstance(related, str):
            related = (related,)
        return cls(
            source_id=item.source.source_id,
            source_type=source_type,
            project_id=item.project.project_id,
            tenant_id=item.project.tenant_id,
            title=str(metadata.get("title") or ""),
            raw_uri=item.source.url or f"{item.source.system}:{item.source.source_id}",
            revision=revision,
            observed_at=item.observed_at,
            effective_from=_parse_datetime(metadata.get("effective_from")),
            effective_to=_parse_datetime(metadata.get("effective_to")),
            owner=_optional_str(metadata.get("owner") or metadata.get("owner_user_id")),
            status=str(metadata.get("status") or "observed"),
            topic=str(metadata.get("topic") or ""),
            authority_scope=tuple(str(value) for value in authority),
            supersedes=_string_tuple(metadata.get("supersedes")),
            related_sources=tuple(str(value) for value in related),
            access_scope=item.access_scope,
            content_hash=item.content_hash,
            raw_content_ref=str(metadata.get("raw_content_ref") or ""),
            content=item.content,
            fact_values={str(k): str(v) for k, v in (metadata.get("fact_values") or {}).items()},
            metadata=metadata,
            revoked=item.revoked,
        )

    def to_evidence(self, *, project: ProjectRef | None = None) -> Evidence:
        item_project = project or ProjectRef(
            tenant_id=self.tenant_id or "unknown",
            project_id=self.project_id,
        )
        metadata = dict(self.metadata)
        metadata.update(
            {
                "source_type": self.source_type.value,
                "title": self.title,
                "revision": self.revision,
                "status": self.status,
                "topic": self.topic,
                "authority_scope": list(self.authority_scope),
                "supersedes": list(self.supersedes),
                "related_sources": list(self.related_sources),
                "raw_uri": self.raw_uri,
                "source_record_key": ":".join(self.key),
            }
        )
        evidence_type = _evidence_type(self.source_type)
        return Evidence(
            type=evidence_type,
            project=item_project,
            source=SourceRef(system=self.source_type.value, source_id=self.source_id, url=self.raw_uri or None),
            content=self.content,
            observed_at=self.observed_at,
            access_scope=self.access_scope,
            content_hash=self.content_hash,
            metadata=metadata,
            revoked=self.revoked,
        )


def _source_type(item: Evidence) -> SourceType:
    raw = str(item.metadata.get("source_type") or "")
    if raw:
        try:
            return SourceType(raw)
        except ValueError:
            pass
    if item.source.system in {"feishu_doc", "feishu_document"}:
        return SourceType.FEISHU_DOCUMENT
    if item.source.system == "feishu_bitable":
        return SourceType.FEISHU_BITABLE
    if item.source.system == "feishu_project":
        return SourceType.FEISHU_PROJECT
    if item.source.system == "feishu_minutes":
        return SourceType.MEETING_MINUTE
    if item.source.system == "github":
        return {
            "commit": SourceType.GITHUB_COMMIT,
            "pull_request": SourceType.GITHUB_PULL_REQUEST,
            "issue": SourceType.GITHUB_ISSUE,
        }.get(str(item.metadata.get("kind")), SourceType.GITHUB_CODE)
    return SourceType.OTHER


def _evidence_type(source_type: SourceType) -> EvidenceType:
    if source_type is SourceType.GITHUB_COMMIT:
        return EvidenceType.COMMIT
    if source_type is SourceType.GITHUB_PULL_REQUEST:
        return EvidenceType.PULL_REQUEST
    if source_type is SourceType.GITHUB_ISSUE or source_type is SourceType.FEISHU_PROJECT:
        return EvidenceType.TASK
    if source_type is SourceType.GITHUB_CODE:
        return EvidenceType.CODE
    return EvidenceType.DOCUMENT


def _default_authority(source_type: SourceType) -> tuple[str, ...]:
    if source_type in {SourceType.FEISHU_DOCUMENT, SourceType.FEISHU_BITABLE}:
        return ("requirement_scope", "technical_decision", "test_status")
    if source_type is SourceType.FEISHU_PROJECT:
        return ("requirement_status", "owner")
    if source_type is SourceType.MEETING_MINUTE:
        return ("requirement_scope", "requirement_status", "development_progress", "test_status", "technical_decision", "owner")
    if source_type in {SourceType.GITHUB_ISSUE, SourceType.GITHUB_PULL_REQUEST, SourceType.GITHUB_COMMIT, SourceType.GITHUB_CODE}:
        return ("development_progress",)
    return ()


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


def _string_tuple(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return ()


__all__ = ["FactType", "SourceRecord", "SourceType"]
