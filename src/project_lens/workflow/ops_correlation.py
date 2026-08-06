"""Read-only ProjectOps correlation over already-collected workflow context."""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import Field

from project_lens.context.models import EvidenceBundle
from project_lens.domain.models import Evidence, EvidenceType, GraphEvidence, ProjectRef
from project_lens.domain.ops import OpsFinding
from project_lens.workflow.models import FrozenModel


class OpsCorrelation(FrozenModel):
    signal_count: int = 0
    signal_counts_by_kind: dict[str, int] = Field(default_factory=dict)
    related_routes: tuple[str, ...] = ()
    related_trace_ids: tuple[str, ...] = ()
    related_metrics: tuple[str, ...] = ()
    related_services: tuple[str, ...] = ()
    related_modules: tuple[str, ...] = ()
    related_symbols: tuple[str, ...] = ()
    related_changes: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    hypotheses: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    evidence_ids: tuple[UUID, ...] = ()


def build_ops_correlation(
    finding: OpsFinding | None,
    *,
    evidence: tuple[Evidence, ...] | None = None,
    bundle: EvidenceBundle | None = None,
    graph_paths: tuple[GraphEvidence, ...] = (),
    project: ProjectRef,
) -> OpsCorrelation | None:
    """Build a traceable correlation summary without querying external stores."""

    if finding is None or not finding.signals:
        return None
    evidence_items = evidence if evidence is not None else (bundle.evidence if bundle else ())
    signal_counts = _signal_counts(finding)
    routes = _routes(finding, evidence_items, graph_paths)
    trace_ids = _trace_ids(finding, evidence_items)
    metrics = _metrics(finding, evidence_items)
    services = _services(finding, evidence_items, graph_paths, project)
    modules = _modules(evidence_items, graph_paths)
    symbols = _symbols(evidence_items, graph_paths)
    changes = _changes(evidence_items, modules)
    evidence_ids = _evidence_ids(finding, evidence_items, routes, trace_ids, metrics, modules, symbols, changes)
    facts = _facts(signal_counts, routes, trace_ids, metrics)
    inferences = _inferences(services, modules, symbols, changes)
    hypotheses = _hypotheses(routes, changes)
    recommendations = _recommendations(routes, modules, symbols)
    return OpsCorrelation(
        signal_count=len(finding.signals),
        signal_counts_by_kind=signal_counts,
        related_routes=routes,
        related_trace_ids=trace_ids,
        related_metrics=metrics,
        related_services=services,
        related_modules=modules,
        related_symbols=symbols,
        related_changes=changes,
        facts=facts,
        inferences=inferences,
        hypotheses=hypotheses,
        recommendations=recommendations,
        evidence_ids=evidence_ids,
    )


def _signal_counts(finding: OpsFinding) -> dict[str, int]:
    counts: dict[str, int] = {}
    for signal in finding.signals:
        key = signal.kind.value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _routes(
    finding: OpsFinding,
    evidence: tuple[Evidence, ...],
    graph_paths: tuple[GraphEvidence, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    for signal in finding.signals:
        values.extend(_route_values(signal.labels))
        values.extend(_route_values(signal.metadata))
    for item in evidence:
        values.extend(_route_values(item.metadata))
        values.extend(_route_tokens(item.content))
    for path in graph_paths:
        values.extend(label for label in path.path_labels if label.startswith("/"))
    return _unique(values)


def _route_values(mapping: dict) -> list[str]:
    values: list[str] = []
    for key in ("route", "endpoint_path", "endpoint", "path"):
        raw = mapping.get(key)
        if isinstance(raw, str) and raw.startswith("/"):
            values.append(raw)
    return values


def _route_tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"/[A-Za-z0-9_./{}:-]+", text)
        if not token.endswith(".py") and "." not in token
    ]


def _trace_ids(finding: OpsFinding, evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for signal in finding.signals:
        if signal.trace_id:
            values.append(signal.trace_id)
    for item in evidence:
        raw = item.metadata.get("trace_id")
        if isinstance(raw, str) and raw:
            values.append(raw)
    return _unique(values)


def _metrics(finding: OpsFinding, evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for signal in finding.signals:
        if signal.metric_name:
            values.append(signal.metric_name)
    for item in evidence:
        raw = item.metadata.get("metric_name")
        if isinstance(raw, str) and raw:
            values.append(raw)
    return _unique(values)


def _services(
    finding: OpsFinding,
    evidence: tuple[Evidence, ...],
    graph_paths: tuple[GraphEvidence, ...],
    project: ProjectRef,
) -> tuple[str, ...]:
    values: list[str] = []
    for signal in finding.signals:
        if signal.service:
            values.append(signal.service)
    values.extend(item.project.service for item in evidence if item.project.service)
    values.extend(
        label
        for path in graph_paths
        for label in path.path_labels
        if label == project.service
    )
    return _unique(values)


def _modules(
    evidence: tuple[Evidence, ...],
    graph_paths: tuple[GraphEvidence, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    for item in evidence:
        for key in ("module", "file"):
            raw = item.metadata.get(key)
            if isinstance(raw, str) and raw:
                values.append(raw)
        raw_files = item.metadata.get("files_changed")
        if isinstance(raw_files, (list, tuple)):
            values.extend(str(value) for value in raw_files if str(value))
    for path in graph_paths:
        values.extend(
            label
            for label in path.path_labels
            if label.endswith(".py") or "/" in label and not label.startswith("/")
        )
    return _unique(values)


def _symbols(
    evidence: tuple[Evidence, ...],
    graph_paths: tuple[GraphEvidence, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    for item in evidence:
        raw = item.metadata.get("symbol")
        if isinstance(raw, str) and raw:
            values.append(raw)
    for path in graph_paths:
        values.extend(label for label in path.path_labels if _looks_like_symbol(label))
    return _unique(values)


def _looks_like_symbol(label: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{2,}", label))


def _changes(evidence: tuple[Evidence, ...], modules: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for item in evidence:
        if item.type != EvidenceType.COMMIT:
            continue
        changed = item.metadata.get("files_changed")
        changed_files = tuple(str(value) for value in changed) if isinstance(changed, (list, tuple)) else ()
        if modules and changed_files and not set(changed_files).intersection(modules):
            continue
        sha = str(item.metadata.get("commit_sha") or item.source.source_id)
        subject = item.content.splitlines()[0] if item.content else item.source.source_id
        values.append(f"{sha}: {subject}")
    return _unique(values)


def _evidence_ids(
    finding: OpsFinding,
    evidence: tuple[Evidence, ...],
    routes: tuple[str, ...],
    trace_ids: tuple[str, ...],
    metrics: tuple[str, ...],
    modules: tuple[str, ...],
    symbols: tuple[str, ...],
    changes: tuple[str, ...],
) -> tuple[UUID, ...]:
    ids: list[UUID] = [item.id for item in finding.evidence]
    needles = routes + trace_ids + metrics + modules + symbols + changes
    for item in evidence:
        text = f"{item.content}\n{item.source.source_id}\n{item.metadata}"
        if not needles or any(needle and needle in text for needle in needles):
            ids.append(item.id)
    return tuple(dict.fromkeys(ids))


def _facts(
    signal_counts: dict[str, int],
    routes: tuple[str, ...],
    trace_ids: tuple[str, ...],
    metrics: tuple[str, ...],
) -> tuple[str, ...]:
    counts_text = ", ".join(f"{key}={value}" for key, value in signal_counts.items())
    facts = [f"ProjectOps time-window signals matched: {counts_text}."]
    if routes:
        facts.append(f"Routes observed in ops signals: {', '.join(routes)}.")
    if trace_ids:
        facts.append(f"Trace ids observed in ops signals: {', '.join(trace_ids)}.")
    if metrics:
        facts.append(f"Metrics observed in ops signals: {', '.join(metrics)}.")
    return tuple(facts)


def _inferences(
    services: tuple[str, ...],
    modules: tuple[str, ...],
    symbols: tuple[str, ...],
    changes: tuple[str, ...],
) -> tuple[str, ...]:
    parts: list[str] = []
    if services:
        parts.append(f"services={', '.join(services)}")
    if modules:
        parts.append(f"modules={', '.join(modules[:4])}")
    if symbols:
        parts.append(f"symbols={', '.join(symbols[:4])}")
    if changes:
        parts.append(f"recent_changes={', '.join(changes[:3])}")
    if not parts:
        return ()
    return (f"ProjectOps correlation points to {'; '.join(parts)}.",)


def _hypotheses(routes: tuple[str, ...], changes: tuple[str, ...]) -> tuple[str, ...]:
    if routes and changes:
        return (
            "The endpoint anomaly may be related to recent code changes, but root cause still needs reproduction or trace-level confirmation.",
        )
    return (
        "Root cause is not fully confirmed from the current ops window; use the matched signals as investigation anchors.",
    )


def _recommendations(
    routes: tuple[str, ...],
    modules: tuple[str, ...],
    symbols: tuple[str, ...],
) -> tuple[str, ...]:
    target = routes[0] if routes else "the affected endpoint"
    module = modules[0] if modules else "the candidate module"
    symbol = symbols[0] if symbols else "the candidate function"
    return (
        f"Read-only next step: inspect {target}, {module}, and {symbol}; verify with tests before any Apply/PR/deploy action.",
    )


def _unique(values: list[str]) -> tuple[str, ...]:
    cleaned = [value.strip() for value in values if value and value.strip()]
    return tuple(dict.fromkeys(cleaned))
