"""Markdown/text document and JSON incident indexing."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from project_lens.context.chunking import split_text
from project_lens.context.indexing.common import content_hash, utc_now
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef


class DocumentIndexer:
    _supported = {".md", ".txt"}

    def index(
        self,
        root: Path,
        *,
        project: ProjectRef,
        access_scope: str,
        observed_at: datetime | None = None,
    ) -> list[Evidence]:
        root = root.resolve()
        evidence: list[Evidence] = []
        for path in sorted(item for item in root.rglob("*") if item.suffix.lower() in self._supported):
            text = path.read_text(encoding="utf-8", errors="replace")
            relative_path = path.relative_to(root).as_posix()
            chunks = split_text(text)
            for index, chunk in enumerate(chunks):
                evidence.append(
                    Evidence(
                        type=EvidenceType.DOCUMENT,
                        project=project,
                        source=SourceRef(
                            system="local_document",
                            source_id=f"{relative_path}#chunk-{index}",
                        ),
                        content=chunk,
                        observed_at=observed_at or utc_now(),
                        access_scope=access_scope,
                        content_hash=content_hash(chunk),
                        metadata={
                            "file": relative_path,
                            "chunk_index": index,
                            "total_chunks": len(chunks),
                        },
                    )
                )
        return evidence


class IncidentIndexer:
    def index_file(
        self,
        path: Path,
        *,
        project: ProjectRef,
        access_scope: str,
    ) -> list[Evidence]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else payload.get("incidents", [])
        if not isinstance(records, list):
            raise ValueError("incident file must contain a list or an incidents list")
        evidence: list[Evidence] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            incident_id = str(record.get("id") or f"incident-{index}")
            observed_at = _parse_datetime(record.get("occurred_at"))
            content = _incident_content(record)
            item_project = project.model_copy(
                update={"service": record.get("service") or project.service}
            )
            evidence.append(
                Evidence(
                    type=EvidenceType.INCIDENT,
                    project=item_project,
                    source=SourceRef(system="incident_archive", source_id=incident_id),
                    content=content,
                    observed_at=observed_at,
                    access_scope=access_scope,
                    content_hash=content_hash(content),
                    metadata={key: value for key, value in record.items() if key != "content"},
                )
            )
        return evidence


def _incident_content(record: dict[str, Any]) -> str:
    fields = ("title", "summary", "root_cause", "resolution", "impact")
    parts = [f"{field}: {record[field]}" for field in fields if record.get(field)]
    return "\n".join(parts) or json.dumps(record, ensure_ascii=False, sort_keys=True)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed
    return utc_now()

