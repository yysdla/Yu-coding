"""Time-window ops queries that emit ephemeral Evidence for the current run."""

from __future__ import annotations

from project_lens.context.indexing.common import content_hash
from project_lens.context.models import AccessContext
from project_lens.context.ops.store import InMemoryOpsSignalStore
from project_lens.domain.models import Evidence, EvidenceType, SourceRef
from project_lens.domain.ops import (
    OperationalSignal,
    OpsFinding,
    OpsQuery,
    OpsSignalKind,
)


class OpsQueryService:
    def __init__(self, store: InMemoryOpsSignalStore | None = None) -> None:
        self._store = store or InMemoryOpsSignalStore()

    @property
    def store(self) -> InMemoryOpsSignalStore:
        return self._store

    def query(
        self,
        query: OpsQuery,
        access: AccessContext,
    ) -> OpsFinding:
        matched = [
            signal
            for signal in self._store.all()
            if _matches(signal, query, access)
        ]
        matched.sort(key=lambda item: item.observed_at, reverse=True)
        matched = matched[: query.limit]
        evidence = tuple(_to_ephemeral_evidence(signal, query) for signal in matched)
        if not matched:
            return OpsFinding(
                query=query,
                signals=(),
                evidence=(),
                summary="当前时间窗内没有可访问的运维信号。",
                warnings=("no authorized ops signals in time window",),
            )
        summary = _summarize(matched)
        return OpsFinding(
            query=query,
            signals=tuple(matched),
            evidence=evidence,
            summary=summary,
        )

    def query_logs(self, query: OpsQuery, access: AccessContext) -> OpsFinding:
        return self.query(
            query.model_copy(update={"kinds": (OpsSignalKind.LOG,)}),
            access,
        )

    def query_metrics(self, query: OpsQuery, access: AccessContext) -> OpsFinding:
        return self.query(
            query.model_copy(update={"kinds": (OpsSignalKind.METRIC,)}),
            access,
        )

    def query_traces(self, query: OpsQuery, access: AccessContext) -> OpsFinding:
        return self.query(
            query.model_copy(update={"kinds": (OpsSignalKind.TRACE,)}),
            access,
        )


def _matches(signal: OperationalSignal, query: OpsQuery, access: AccessContext) -> bool:
    if signal.project.tenant_id != access.tenant_id:
        return False
    if signal.project.project_id != query.project.project_id:
        return False
    if signal.access_scope not in access.permissions:
        return False
    if signal.kind not in query.kinds:
        return False
    if not (query.start <= signal.observed_at <= query.end):
        return False
    environment = query.environment or query.project.environment
    if environment and signal.environment != environment:
        return False
    service = query.service or query.project.service
    if service and signal.service not in {None, service}:
        return False
    if query.metric_name and signal.metric_name != query.metric_name:
        return False
    if query.trace_id and signal.trace_id != query.trace_id:
        return False
    if query.text_filter:
        haystack = f"{signal.summary} {signal.trace_id or ''} {' '.join(signal.labels.values())}"
        if query.text_filter.casefold() not in haystack.casefold():
            return False
    return True


def _to_ephemeral_evidence(signal: OperationalSignal, query: OpsQuery) -> Evidence:
    evidence_type = {
        OpsSignalKind.LOG: EvidenceType.LOG,
        OpsSignalKind.METRIC: EvidenceType.METRIC,
        OpsSignalKind.TRACE: EvidenceType.LOG,
    }[signal.kind]
    content = _signal_content(signal)
    window = f"{query.start.isoformat()}..{query.end.isoformat()}"
    return Evidence(
        type=evidence_type,
        project=signal.project.model_copy(
            update={
                "service": signal.service or signal.project.service,
                "environment": signal.environment,
            }
        ),
        source=SourceRef(
            system="ops_window",
            source_id=f"{signal.kind.value}:{signal.id}",
        ),
        content=content,
        observed_at=signal.observed_at,
        access_scope=signal.access_scope,
        content_hash=content_hash(f"{signal.id}:{window}:{content}"),
        metadata={
            "ops_kind": signal.kind.value,
            "ephemeral": True,
            "time_window": window,
            "trace_id": signal.trace_id,
            "metric_name": signal.metric_name,
            "metric_value": signal.metric_value,
            "level": signal.level,
            "labels": signal.labels,
        },
    )


def _signal_content(signal: OperationalSignal) -> str:
    parts = [f"kind: {signal.kind.value}", f"summary: {signal.summary}"]
    if signal.level:
        parts.append(f"level: {signal.level}")
    if signal.metric_name is not None:
        parts.append(f"metric: {signal.metric_name}={signal.metric_value}")
    if signal.trace_id:
        parts.append(f"trace_id: {signal.trace_id}")
    if signal.labels:
        label_text = ", ".join(f"{key}={value}" for key, value in sorted(signal.labels.items()))
        parts.append(f"labels: {label_text}")
    return "\n".join(parts)


def _summarize(signals: list[OperationalSignal]) -> str:
    counts: dict[str, int] = {}
    for signal in signals:
        counts[signal.kind.value] = counts.get(signal.kind.value, 0) + 1
    coverage = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    head = signals[0].summary
    return f"时间窗内命中运维信号（{coverage}）。最新：{head}"
