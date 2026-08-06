"""Build project snapshots from authorized evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, ProjectSnapshot


def build_project_snapshot(
    evidence: Iterable[Evidence],
    *,
    project: ProjectRef,
) -> ProjectSnapshot:
    items = tuple(evidence)
    services: list[str] = []
    entrypoints: list[str] = []
    dependencies: list[str] = []
    risks: list[str] = []
    unresolved: list[str] = []
    evidence_ids = []

    if items and project.service:
        services.append(project.service)

    for item in items:
        evidence_ids.append(item.id)
        content = item.content
        if item.project.service:
            services.append(item.project.service)
        services.extend(_service_names(content))
        if item.type == EvidenceType.CODE:
            symbol = item.metadata.get("symbol")
            symbol_type = item.metadata.get("symbol_type")
            if symbol and symbol_type in {"function", "module"}:
                entrypoints.append(str(symbol))
        entrypoints.extend(_backticked_symbols(content))
        dependencies.extend(_dependencies(content))
        risks.extend(_risks(item))

    if not services:
        unresolved.append("缺少服务归属证据。")
    if not entrypoints:
        unresolved.append("缺少入口函数或模块证据。")
    if not dependencies:
        unresolved.append("缺少上下游依赖证据。")
    if not risks:
        unresolved.append("缺少风险、事故或边界条件证据。")

    return ProjectSnapshot(
        project=project,
        services=_dedupe(services),
        entrypoints=_dedupe(entrypoints),
        dependencies=_dedupe(dependencies),
        risks=_dedupe(risks),
        unresolved_items=tuple(unresolved),
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
    )


def _service_names(content: str) -> list[str]:
    names = re.findall(r"\b[a-z][a-z0-9-]* service\b", content, flags=re.IGNORECASE)
    return [name.lower() for name in names]


def _backticked_symbols(content: str) -> list[str]:
    return [
        item
        for item in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", content)
        if item.lower().endswith(("order", "service")) or item.startswith(("create_", "build_"))
    ]


def _dependencies(content: str) -> list[str]:
    lowered = content.lower()
    dependencies: list[str] = []
    if "payment service" in lowered:
        dependencies.append("payment service")
    if "coupon" in lowered:
        dependencies.append("coupon data")
    return dependencies


def _risks(item: Evidence) -> list[str]:
    lowered = item.content.lower()
    risks: list[str] = []
    if "optional" in lowered and "coupon" in lowered:
        risks.append("optional coupon handling")
    if item.type == EvidenceType.INCIDENT:
        if "http 500" in lowered:
            risks.append("checkout HTTP 500")
        if "root_cause" in lowered or "null guard" in lowered:
            risks.append("coupon null guard regression")
    return risks


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    cleaned = [item.strip() for item in items if item and item.strip()]
    return tuple(dict.fromkeys(cleaned))
