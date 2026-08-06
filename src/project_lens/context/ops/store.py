"""In-memory operational signal store loaded from local fixtures."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from project_lens.domain.models import ProjectRef
from project_lens.domain.ops import OperationalSignal, OpsSignalKind


class InMemoryOpsSignalStore:
    def __init__(self, signals: Iterable[OperationalSignal] = ()) -> None:
        self._signals: list[OperationalSignal] = list(signals)

    def add_many(self, signals: Iterable[OperationalSignal]) -> None:
        self._signals.extend(signals)

    def all(self) -> tuple[OperationalSignal, ...]:
        return tuple(self._signals)


def load_ops_signals_file(path: Path) -> list[OperationalSignal]:
    payload: dict[str, Any] | list[Any] = json.loads(path.read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else payload.get("signals", [])
    if not isinstance(records, list):
        raise ValueError("ops signals file must contain a list or a signals list")
    signals: list[OperationalSignal] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        try:
            signals.append(_signal_from_record(record, index=index))
        except (KeyError, TypeError, ValueError):
            continue
    return signals


def _signal_from_record(record: dict[str, Any], *, index: int) -> OperationalSignal:
    kind = OpsSignalKind(str(record["kind"]))
    project = ProjectRef(
        tenant_id=str(record["tenant_id"]),
        project_id=str(record["project_id"]),
        service=_optional_str(record.get("service")),
        environment=_optional_str(record.get("environment")),
    )
    observed_at = _parse_datetime(record.get("observed_at"))
    summary = str(record.get("summary") or record.get("message") or "").strip()
    if not summary:
        raise ValueError("ops signal summary is empty")
    labels_raw = record.get("labels") or {}
    labels = (
        {str(key): str(value) for key, value in labels_raw.items()}
        if isinstance(labels_raw, dict)
        else {}
    )
    return OperationalSignal(
        id=uuid4(),
        kind=kind,
        project=project,
        environment=str(record.get("environment") or project.environment or "production"),
        observed_at=observed_at,
        access_scope=str(record["access_scope"]),
        summary=summary,
        service=_optional_str(record.get("service")),
        level=_optional_str(record.get("level")),
        metric_name=_optional_str(record.get("metric_name") or record.get("name")),
        metric_value=_optional_float(record.get("metric_value") or record.get("value")),
        trace_id=_optional_str(record.get("trace_id")),
        labels=labels,
        metadata={
            "source_index": index,
            **{
                key: value
                for key, value in record.items()
                if key
                not in {
                    "kind",
                    "tenant_id",
                    "project_id",
                    "service",
                    "environment",
                    "observed_at",
                    "access_scope",
                    "summary",
                    "message",
                    "level",
                    "metric_name",
                    "name",
                    "metric_value",
                    "value",
                    "trace_id",
                    "labels",
                }
            },
        },
    )


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError("observed_at is required")


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
