"""Index local service-dependency fixtures into DOCUMENT evidence for the graph."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from project_lens.context.indexing.common import content_hash, utc_now
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef


class DependencyIndexer:
    """Fixture-first service dependency edges (Phase 7)."""

    def index_file(
        self,
        path: Path,
        *,
        project: ProjectRef,
        access_scope: str,
    ) -> list[Evidence]:
        payload: dict[str, Any] | list[Any] = json.loads(path.read_text(encoding="utf-8"))
        records_raw = (
            payload if isinstance(payload, list) else payload.get("dependencies", [])
        )
        if not isinstance(records_raw, list):
            raise ValueError("dependencies file must contain a list or a dependencies list")
        return self.index(records_raw, project=project, access_scope=access_scope)

    def index(
        self,
        records: Iterable[dict[str, Any]],
        *,
        project: ProjectRef,
        access_scope: str,
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            from_service = str(record.get("from_service") or "").strip()
            to_service = str(record.get("to_service") or "").strip()
            if not from_service or not to_service:
                continue
            relation = str(record.get("relation") or "depends_on").strip() or "depends_on"
            summary = str(record.get("summary") or "").strip()
            content = (
                f"service_dependency: {from_service} {relation} {to_service}\n"
                f"{summary}".strip()
            )
            scope = str(record.get("access_scope") or access_scope)
            item_project = project.model_copy(update={"service": from_service})
            source_id = f"{from_service}->{to_service}#{index}"
            evidence.append(
                Evidence(
                    type=EvidenceType.DOCUMENT,
                    project=item_project,
                    source=SourceRef(system="local_dependency", source_id=source_id),
                    content=content,
                    observed_at=utc_now(),
                    access_scope=scope,
                    content_hash=content_hash(content),
                    metadata={
                        "kind": "service_dependency",
                        "from_service": from_service,
                        "to_service": to_service,
                        "relation": relation,
                        "summary": summary or None,
                    },
                )
            )
        return evidence
