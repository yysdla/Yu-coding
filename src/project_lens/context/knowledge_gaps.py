"""Build structured knowledge-gap reports from ACL-filtered evidence only."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import PurePosixPath
from uuid import UUID

from project_lens.context.snapshot import build_project_snapshot
from project_lens.domain.models import (
    Evidence,
    EvidenceType,
    KnowledgeGap,
    KnowledgeGapReport,
    KnowledgeGapSeverity,
    KnowledgeGapType,
    ProjectRef,
    ProjectSnapshot,
)
from project_lens.graph.models import GraphNode, GraphNodeKind, ProjectGraph

_API_MARKERS = (
    "openapi",
    "swagger",
    "endpoint",
    "api doc",
    "api-doc",
    "接口说明",
    "接口文档",
    "rest api",
    "@app.route",
    "fastapi",
    "router.",
)
_RUNBOOK_MARKERS = (
    "runbook",
    "操作手册",
    "运维手册",
    "应急手册",
    "on-call",
    "oncall",
    "sop",
    "playbook",
    "处置步骤",
    "故障处置",
)
_ARCHITECTURE_MARKERS = (
    "architecture",
    "架构",
    "模块职责",
    "系统结构",
    "dependency",
    "依赖",
)
_MODULE_PATH_RE = re.compile(
    r"(?:^|[\s`\"'(])((?:src|examples|tests)/[\w./\\-]+\.py)\b",
    re.IGNORECASE,
)


def build_knowledge_gap_report(
    evidence: Iterable[Evidence],
    *,
    project: ProjectRef,
    snapshot: ProjectSnapshot | None = None,
    graph: ProjectGraph | None = None,
) -> KnowledgeGapReport:
    """Derive gaps from already ACL-filtered evidence. Never pass unfiltered indexes."""

    items = tuple(evidence)
    snapshot = snapshot or build_project_snapshot(items, project=project)
    coverage = Counter(item.type.value for item in items)
    gaps: list[KnowledgeGap] = []

    if not items:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.ACCESS_LIMITED,
                title="当前权限下无项目证据",
                description="ACL 过滤后没有任何可访问证据，无法评估知识库覆盖。",
                recommendation="确认用户项目权限，或补充可授权的项目资料后再查询。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal="access_limited:no_authorized_evidence",
            )
        )
        return KnowledgeGapReport(
            project=project,
            gaps=tuple(gaps),
            type_coverage={},
            signal_coverage={},
            evidence_ids=(),
        )

    for unresolved in snapshot.unresolved_items:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.ARCHITECTURE,
                title="快照未解决项",
                description=unresolved,
                recommendation="补充对应主题的架构/代码/风险证据后重新生成项目快照。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal="snapshot.unresolved_items",
                evidence_ids=snapshot.evidence_ids[:3],
            )
        )

    if coverage.get(EvidenceType.CODE.value, 0) == 0:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.CODE_COVERAGE,
                title="缺少代码证据",
                description="当前可访问证据中没有 CODE 类型。",
                recommendation="补充可索引的项目代码或关键模块源文件。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal="missing_evidence_type:code",
            )
        )

    if coverage.get(EvidenceType.DOCUMENT.value, 0) == 0:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.ARCHITECTURE,
                title="缺少文档证据",
                description="当前可访问证据中没有 DOCUMENT 类型。",
                recommendation="补充架构说明、决策记录或团队文档。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal="missing_evidence_type:document",
            )
        )

    if coverage.get(EvidenceType.COMMIT.value, 0) == 0:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.VERSION_HISTORY,
                title="缺少版本变更证据",
                description="当前可访问证据中没有 COMMIT 类型。",
                recommendation="补充 Git 变更记录或发布相关提交。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal="missing_evidence_type:commit",
            )
        )

    if coverage.get(EvidenceType.TASK.value, 0) == 0:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.TASK_TRACKING,
                title="缺少任务跟踪证据",
                description="当前可访问证据中没有 TASK 类型。",
                recommendation="补充任务、工单或发布跟踪记录。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal="missing_evidence_type:task",
            )
        )

    if coverage.get(EvidenceType.INCIDENT.value, 0) == 0:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.INCIDENT_REVIEW,
                title="缺少事故复盘证据",
                description="当前可访问证据中没有 INCIDENT 类型。",
                recommendation="补充事故复盘、故障时间线或 postmortem。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal="missing_evidence_type:incident",
            )
        )

    if not _has_owner_signal(items):
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.OWNER,
                title="缺少负责人信息",
                description="未找到 owner / 负责人相关证据或元数据。",
                recommendation="补充负责人文档、OWNER 字段或团队职责说明。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal="missing_project_signal:owner",
            )
        )

    if not _has_decision_signal(items):
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.DECISION_RECORD,
                title="缺少决策记录",
                description="未找到决策记录、ADR 或明确决策文档信号。",
                recommendation="补充决策记录或在文档中标注已确认决策。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal="missing_project_signal:decision_record",
            )
        )

    if not _has_runbook_signal(items):
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.RUNBOOK,
                title="缺少运维手册 / Runbook",
                description="未找到 runbook、操作手册、应急处置或 on-call 相关证据。",
                recommendation="补充故障处置手册、SOP 或 on-call runbook，并关联服务名。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal="missing_project_signal:runbook",
                evidence_ids=_sample_evidence_ids(items, limit=3),
                target_ref="project:runbooks",
                suggested_owners=_collect_suggested_owners(items)[:4],
            )
        )

    if not _has_incident_review_signal(items) and coverage.get(EvidenceType.INCIDENT.value, 0) > 0:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.INCIDENT_REVIEW,
                title="事故复盘信息不完整",
                description="已有事故证据，但缺少 root_cause / resolution / postmortem 等复盘字段。",
                recommendation="补全事故复盘字段后再用于协作诊断。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal="incomplete_project_signal:incident_review",
                evidence_ids=tuple(
                    item.id for item in items if item.type == EvidenceType.INCIDENT
                )[:3],
                suggested_owners=_collect_suggested_owners(items),
            )
        )

    gaps.extend(_metadata_gaps(items))
    # Entity-aware loop: service/module/release/api gaps with suggested owners.
    gaps.extend(_entity_aware_gaps(items, project=project))
    if graph is not None:
        gaps.extend(_graph_aware_gaps(graph, items, project=project))

    return KnowledgeGapReport(
        project=project,
        gaps=tuple(_dedupe_gaps(gaps)),
        type_coverage=dict(sorted(coverage.items())),
        signal_coverage=_signal_coverage(items),
        evidence_ids=tuple(dict.fromkeys(item.id for item in items)),
    )


def is_knowledge_gap_question(question: str) -> bool:
    normalized = "".join(question.casefold().split())
    return any(
        marker in normalized
        for marker in ("知识库缺什么", "知识缺口", "资料缺口", "缺失证据", "缺少什么资料")
    )


def _has_owner_signal(evidence: tuple[Evidence, ...]) -> bool:
    for item in evidence:
        if item.metadata.get("owner") or item.metadata.get("owner_user_id"):
            return True
        if item.metadata.get("assignee"):
            return True
        if _content_has_owner_signal(item.content):
            return True
    return False


def _content_has_owner_signal(content: str) -> bool:
    """Avoid false positives like 'without owners'."""

    haystack = content.casefold()
    if "负责人" in haystack or "maintainer" in haystack:
        return True
    if re.search(r"\bresponsible(?:\s+for)?\b", haystack):
        return True
    if re.search(r"\bowned\s+by\b", haystack):
        return True
    if re.search(r"\bowner(?:_user_id)?\s*[:=]", haystack):
        return True
    if re.search(r"\bowners?\s*:", haystack):
        return True
    return False


def _has_decision_signal(evidence: tuple[Evidence, ...]) -> bool:
    markers = ("decision", "决策", "adr", "rfc")
    for item in evidence:
        haystack = f"{item.content} {item.source.source_id}".casefold()
        if any(marker in haystack for marker in markers):
            return True
    return False


def _has_incident_review_signal(evidence: tuple[Evidence, ...]) -> bool:
    for item in evidence:
        if item.type != EvidenceType.INCIDENT:
            continue
        lowered = item.content.casefold()
        if any(marker in lowered for marker in ("root_cause", "resolution", "postmortem", "复盘")):
            return True
        if item.metadata.get("root_cause") or item.metadata.get("resolution"):
            return True
    return False


def _metadata_gaps(evidence: tuple[Evidence, ...]) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    docs = [item for item in evidence if item.type == EvidenceType.DOCUMENT]
    commits = [item for item in evidence if item.type == EvidenceType.COMMIT]
    if docs and not any(item.metadata.get("revision") or item.source.url for item in docs):
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.ARCHITECTURE,
                title="文档缺少 revision/url 元数据",
                description="可访问文档未提供 revision 或 url，溯源能力不足。",
                recommendation="为文档索引补充 revision 与 source.url。",
                severity=KnowledgeGapSeverity.LOW,
                source_signal="missing_metadata:document_revision_or_url",
                evidence_ids=tuple(item.id for item in docs[:3]),
            )
        )
    if commits and not any(item.metadata.get("branch") for item in commits):
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.VERSION_HISTORY,
                title="提交缺少 branch 元数据",
                description="可访问 COMMIT 证据未提供 branch 字段。",
                recommendation="为 Git 索引补充 branch 元数据。",
                severity=KnowledgeGapSeverity.LOW,
                source_signal="missing_metadata:commit_branch",
                evidence_ids=tuple(item.id for item in commits[:3]),
            )
        )
    return gaps


def _entity_aware_gaps(
    evidence: tuple[Evidence, ...],
    *,
    project: ProjectRef,
) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    owners = _collect_suggested_owners(evidence)
    services = _collect_services(evidence, project=project)
    owned_services = _services_with_owner(evidence)
    for service in services:
        if service in owned_services:
            continue
        service_owners = _owners_for_service(evidence, service) or owners
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.OWNER,
                title=f"服务缺少负责人：{service}",
                description=f"未找到服务 {service} 的 owner / 负责人证据。",
                recommendation=f"为 {service} 补充 OWNER 文档或 owner_user_id 元数据。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal=f"missing_service_owner:{service}",
                evidence_ids=_evidence_ids_for_service(evidence, service),
                target_ref=f"service:{service}",
                suggested_owners=service_owners[:4],
            )
        )

    docs = [item for item in evidence if item.type == EvidenceType.DOCUMENT]
    doc_blob = "\n".join(item.content for item in docs).casefold()
    modules = _collect_code_modules(evidence)
    uncovered = [
        module
        for module in modules
        if PurePosixPath(module).name.casefold() not in doc_blob
        and module.casefold() not in doc_blob
    ]
    for module in uncovered[:4]:
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.ARCHITECTURE,
                title=f"模块缺少架构文档覆盖：{module}",
                description=(
                    f"代码模块 {module} 出现在 CODE/COMMIT 证据中，"
                    "但 DOCUMENT 未描述该模块职责或入口。"
                ),
                recommendation=f"补充 {module} 的架构说明，并标明入口与依赖。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal=f"missing_module_architecture:{module}",
                evidence_ids=_evidence_ids_for_module(evidence, module),
                target_ref=f"module:{module}",
                suggested_owners=owners[:4],
            )
        )

    if not _has_release_signal(evidence):
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.RELEASE_HISTORY,
                title="缺少发布记录",
                description="当前可访问证据中没有 release 类型发布记录。",
                recommendation="补充发布版本、关联 commit 和发布说明。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal="missing_evidence_kind:release",
                evidence_ids=_sample_evidence_ids(evidence, limit=3),
                target_ref="project:releases",
                suggested_owners=owners[:4],
            )
        )

    if not _has_api_doc_signal(evidence):
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.API_DOC,
                title="缺少接口说明",
                description="未找到 OpenAPI/接口文档/endpoint 相关证据。",
                recommendation="补充接口说明、OpenAPI 或关键入口路由文档。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal="missing_project_signal:api_doc",
                evidence_ids=_sample_evidence_ids(evidence, limit=3),
                target_ref="project:api_docs",
                suggested_owners=owners[:4],
            )
        )

    for item in evidence:
        if item.type != EvidenceType.INCIDENT:
            continue
        if _incident_has_review(item):
            continue
        incident_id = str(
            item.metadata.get("incident_id")
            or item.metadata.get("id")
            or item.source.source_id
        )
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.INCIDENT_REVIEW,
                title=f"故障复盘不完整：{incident_id}",
                description=f"{incident_id} 缺少 root_cause / resolution / 复盘字段。",
                recommendation=f"由负责人补全 {incident_id} 的根因与修复结论。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal=f"incomplete_incident_review:{incident_id}",
                evidence_ids=(item.id,),
                target_ref=f"incident:{incident_id}",
                suggested_owners=_owners_for_service(
                    evidence, str(item.metadata.get("service") or "")
                )
                or owners[:4],
            )
        )
    return gaps


def _sample_evidence_ids(
    evidence: tuple[Evidence, ...],
    *,
    limit: int = 3,
) -> tuple[UUID, ...]:
    """Cite searched evidence when a project-level signal is missing."""

    return tuple(item.id for item in evidence[:limit])


def _evidence_ids_for_service(
    evidence: tuple[Evidence, ...],
    service: str,
) -> tuple[UUID, ...]:
    ids: list[UUID] = []
    for item in evidence:
        svc = str(item.metadata.get("service") or item.project.service or "")
        if svc == service:
            ids.append(item.id)
        if len(ids) >= 3:
            break
    if ids:
        return tuple(ids)
    return _sample_evidence_ids(evidence, limit=2)


def _evidence_ids_for_module(
    evidence: tuple[Evidence, ...],
    module: str,
) -> tuple[UUID, ...]:
    ids: list[UUID] = []
    mod = module.casefold()
    name = PurePosixPath(module).name.casefold()
    for item in evidence:
        if item.type not in {EvidenceType.CODE, EvidenceType.COMMIT}:
            continue
        file_meta = str(item.metadata.get("file") or "")
        files = item.metadata.get("files_changed")
        files_blob = " ".join(str(path) for path in files) if isinstance(files, list) else ""
        blob = f"{item.source.source_id} {file_meta} {files_blob} {item.content}".casefold()
        if mod in blob or name in blob:
            ids.append(item.id)
        if len(ids) >= 3:
            break
    if ids:
        return tuple(ids)
    codeish = tuple(
        item for item in evidence if item.type in {EvidenceType.CODE, EvidenceType.COMMIT}
    )
    return _sample_evidence_ids(codeish or evidence, limit=2)


def _collect_services(
    evidence: tuple[Evidence, ...],
    *,
    project: ProjectRef,
) -> tuple[str, ...]:
    services: list[str] = []
    if project.service:
        services.append(project.service)
    for item in evidence:
        for key in ("service", "service_name"):
            value = item.metadata.get(key)
            if value:
                services.append(str(value))
        if item.project.service:
            services.append(item.project.service)
    return tuple(dict.fromkeys(services))


def _services_with_owner(evidence: tuple[Evidence, ...]) -> set[str]:
    owned: set[str] = set()
    for item in evidence:
        has_owner = bool(
            item.metadata.get("owner")
            or item.metadata.get("owner_user_id")
            or item.metadata.get("assignee")
        )
        content_has_owner = _content_has_owner_signal(item.content)
        if not (has_owner or content_has_owner):
            continue
        service = str(item.metadata.get("service") or item.project.service or "")
        if service:
            owned.add(service)
        # Project-level owner docs cover the project's primary service when present.
        if item.project.service:
            owned.add(item.project.service)
    return owned


def _owners_for_service(
    evidence: tuple[Evidence, ...],
    service: str,
) -> tuple[str, ...]:
    if not service:
        return ()
    owners: list[str] = []
    for item in evidence:
        item_service = str(item.metadata.get("service") or item.project.service or "")
        if item_service and item_service != service:
            continue
        for key in ("owner", "owner_user_id", "assignee"):
            value = item.metadata.get(key)
            if value:
                owners.append(str(value))
    return tuple(dict.fromkeys(owners))


def _collect_suggested_owners(evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    owners: list[str] = []
    for item in evidence:
        for key in ("owner", "owner_user_id", "assignee"):
            value = item.metadata.get(key)
            if value:
                owners.append(str(value))
    return tuple(dict.fromkeys(owners))


def _collect_code_modules(evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    modules: list[str] = []
    for item in evidence:
        if item.type not in {EvidenceType.CODE, EvidenceType.COMMIT}:
            continue
        for key in ("file", "path", "files_changed"):
            raw = item.metadata.get(key)
            if isinstance(raw, list):
                for path in raw:
                    normalized = _normalize_module_path(str(path))
                    if normalized:
                        modules.append(normalized)
            elif raw:
                normalized = _normalize_module_path(str(raw))
                if normalized:
                    modules.append(normalized)
        if item.source.source_id.endswith(".py"):
            normalized = _normalize_module_path(item.source.source_id)
            if normalized:
                modules.append(normalized)
        for match in _MODULE_PATH_RE.finditer(item.content):
            normalized = _normalize_module_path(match.group(1))
            if normalized:
                modules.append(normalized)
    return tuple(dict.fromkeys(modules))


def _normalize_module_path(path: str) -> str | None:
    normalized = path.replace("\\", "/").lstrip("./")
    if not normalized.endswith(".py"):
        return None
    if not any(normalized.startswith(prefix) for prefix in ("src/", "examples/", "tests/")):
        if "/" not in normalized:
            return f"src/{normalized}"
        return None
    return normalized


def _has_release_signal(evidence: tuple[Evidence, ...]) -> bool:
    for item in evidence:
        if str(item.metadata.get("kind") or "").lower() == "release":
            return True
        if item.metadata.get("release_id") or item.metadata.get("version"):
            return True
        haystack = f"{item.content} {item.source.source_id}".casefold()
        if "release" in haystack or "发布" in haystack:
            return True
    return False


def _has_api_doc_signal(evidence: tuple[Evidence, ...]) -> bool:
    for item in evidence:
        haystack = f"{item.content} {item.source.source_id}".casefold()
        if any(marker in haystack for marker in _API_MARKERS):
            return True
        if item.metadata.get("endpoint") or item.metadata.get("openapi"):
            return True
    return False


def _has_runbook_signal(evidence: tuple[Evidence, ...]) -> bool:
    for item in evidence:
        kind = str(item.metadata.get("kind") or item.metadata.get("doc_kind") or "").casefold()
        if kind in {"runbook", "sop", "playbook", "oncall"}:
            return True
        haystack = f"{item.content} {item.source.source_id}".casefold()
        if any(marker in haystack for marker in _RUNBOOK_MARKERS):
            return True
    return False


def _has_architecture_doc_signal(evidence: tuple[Evidence, ...]) -> bool:
    docs = [item for item in evidence if item.type == EvidenceType.DOCUMENT]
    if not docs:
        return False
    for item in docs:
        haystack = f"{item.content} {item.source.source_id}".casefold()
        if any(marker in haystack for marker in _ARCHITECTURE_MARKERS):
            return True
        if item.metadata.get("describes_modules") or item.metadata.get("describes_services"):
            return True
    return False


def _signal_coverage(evidence: tuple[Evidence, ...]) -> dict[str, int]:
    return {
        "architecture_doc": int(_has_architecture_doc_signal(evidence)),
        "api_doc": int(_has_api_doc_signal(evidence)),
        "owner": int(_has_owner_signal(evidence)),
        "runbook": int(_has_runbook_signal(evidence)),
        "release": int(_has_release_signal(evidence)),
        "decision_record": int(_has_decision_signal(evidence)),
        "incident_review": int(_has_incident_review_signal(evidence)),
    }


def _graph_aware_gaps(
    graph: ProjectGraph,
    evidence: tuple[Evidence, ...],
    *,
    project: ProjectRef,
) -> list[KnowledgeGap]:
    """Emit gaps from missing graph relations; cite linked Evidence ids."""

    del project  # project scoping already applied by caller / graph builder input
    gaps: list[KnowledgeGap] = []
    owners = _collect_suggested_owners(evidence)
    by_id = {node.id: node for node in graph.nodes}
    owned_services = {
        edge.target for edge in graph.edges if edge.relation == "responsible_for"
    }
    described_modules = {
        edge.target
        for edge in graph.edges
        if edge.relation == "describes"
        and by_id.get(edge.target) is not None
        and by_id[edge.target].kind == GraphNodeKind.MODULE
    }
    nearby = _sample_graph_summaries(graph, limit=4)

    for node in graph.nodes:
        if node.kind != GraphNodeKind.SERVICE:
            continue
        if node.id in owned_services:
            continue
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.OWNER,
                title=f"图谱缺少负责人边：{node.label}",
                description=(
                    f"图谱中服务 {node.label} 没有 owner -responsible_for-> service 关系，"
                    "无法从 GraphEvidence 追溯负责人。"
                ),
                recommendation=f"补充 {node.label} 的 OWNER 元数据，使图谱生成 responsible_for 边。",
                severity=KnowledgeGapSeverity.HIGH,
                source_signal=f"graph_missing_relation:responsible_for:{node.label}",
                evidence_ids=_evidence_ids_for_graph_node(graph, node, evidence),
                target_ref=f"service:{node.label}",
                suggested_owners=owners[:4],
                graph_summaries=(
                    f"missing: owner -responsible_for-> {node.label}",
                    *nearby[:2],
                ),
            )
        )

    for node in graph.nodes:
        if node.kind != GraphNodeKind.MODULE:
            continue
        if node.id in described_modules:
            continue
        gaps.append(
            KnowledgeGap(
                type=KnowledgeGapType.ARCHITECTURE,
                title=f"图谱缺少文档描述模块：{node.label}",
                description=(
                    f"模块 {node.label} 出现在图谱中，但没有 document -describes-> module 边。"
                ),
                recommendation=f"在架构文档中描述 {node.label}，并写入 describes_modules 元数据。",
                severity=KnowledgeGapSeverity.MEDIUM,
                source_signal=f"graph_missing_relation:document_describes_module:{node.label}",
                evidence_ids=_evidence_ids_for_graph_node(graph, node, evidence),
                target_ref=f"module:{node.label}",
                suggested_owners=owners[:4],
                graph_summaries=(
                    f"missing: document -describes-> {node.label}",
                    *nearby[:2],
                ),
            )
        )

    # Incidents in graph without runbook coverage for the same service.
    if not _has_runbook_signal(evidence):
        for node in graph.nodes:
            if node.kind != GraphNodeKind.INCIDENT:
                continue
            service_label = _service_label_near_node(graph, node)
            target = f"service:{service_label}" if service_label else "project:runbooks"
            gaps.append(
                KnowledgeGap(
                    type=KnowledgeGapType.RUNBOOK,
                    title=f"事故缺少关联 Runbook：{node.label}",
                    description=(
                        f"图谱中存在事故节点 {node.label}，但项目缺少 runbook / 处置手册证据。"
                    ),
                    recommendation="为该事故涉及服务补充应急手册，并在文档中关联 service。",
                    severity=KnowledgeGapSeverity.HIGH,
                    source_signal=f"graph_missing_runbook_for_incident:{node.label}",
                    evidence_ids=_evidence_ids_for_graph_node(graph, node, evidence),
                    target_ref=target,
                    suggested_owners=owners[:4],
                    graph_summaries=(
                        f"incident:{node.label} without runbook coverage",
                        *nearby[:2],
                    ),
                )
            )
    return gaps


def _sample_graph_summaries(graph: ProjectGraph, *, limit: int = 4) -> tuple[str, ...]:
    summaries: list[str] = []
    by_id = {node.id: node for node in graph.nodes}
    for edge in graph.edges:
        source = by_id.get(edge.source)
        target = by_id.get(edge.target)
        if source is None or target is None:
            continue
        if source.kind == GraphNodeKind.EVIDENCE and target.kind == GraphNodeKind.EVIDENCE:
            continue
        summaries.append(f"{source.label} -{edge.relation}-> {target.label}")
        if len(summaries) >= limit:
            break
    return tuple(dict.fromkeys(summaries))


def _evidence_ids_for_graph_node(
    graph: ProjectGraph,
    node: GraphNode,
    evidence: tuple[Evidence, ...],
) -> tuple[UUID, ...]:
    evidence_by_id = {item.id: item for item in evidence}
    ids: list[UUID] = []
    linked = node.metadata.get("evidence_id")
    if linked:
        try:
            evidence_id = UUID(str(linked))
        except ValueError:
            evidence_id = None
        if evidence_id in evidence_by_id:
            ids.append(evidence_id)
    if node.kind == GraphNodeKind.EVIDENCE and node.id.startswith("evidence:"):
        try:
            evidence_id = UUID(node.id.removeprefix("evidence:"))
        except ValueError:
            evidence_id = None
        if evidence_id in evidence_by_id:
            ids.append(evidence_id)
    for edge in graph.edges:
        if edge.target != node.id and edge.source != node.id:
            continue
        other_id = edge.source if edge.target == node.id else edge.target
        if not other_id.startswith("evidence:"):
            continue
        try:
            evidence_id = UUID(other_id.removeprefix("evidence:"))
        except ValueError:
            continue
        if evidence_id in evidence_by_id:
            ids.append(evidence_id)
        if len(ids) >= 3:
            break
    if ids:
        return tuple(dict.fromkeys(ids))[:3]
    return _sample_evidence_ids(evidence, limit=2)


def _service_label_near_node(graph: ProjectGraph, node: GraphNode) -> str | None:
    by_id = {item.id: item for item in graph.nodes}
    for edge in graph.edges:
        if edge.source != node.id and edge.target != node.id:
            continue
        other = by_id.get(edge.target if edge.source == node.id else edge.source)
        if other is not None and other.kind == GraphNodeKind.SERVICE:
            return other.label
    return None


def _incident_has_review(item: Evidence) -> bool:
    lowered = item.content.casefold()
    if any(marker in lowered for marker in ("root_cause", "resolution", "postmortem", "复盘")):
        return True
    return bool(item.metadata.get("root_cause") or item.metadata.get("resolution"))


def _dedupe_gaps(gaps: list[KnowledgeGap]) -> list[KnowledgeGap]:
    """Dedupe by signal; for same type+target prefer graph-derived gaps."""

    by_signal: set[str] = set()
    by_target: dict[tuple[str, str], KnowledgeGap] = {}
    project_level: list[KnowledgeGap] = []
    for gap in gaps:
        signal_key = f"{gap.type.value}:{gap.source_signal}:{gap.target_ref or ''}"
        if signal_key in by_signal:
            continue
        by_signal.add(signal_key)
        if not gap.target_ref:
            project_level.append(gap)
            continue
        target_key = (gap.type.value, gap.target_ref)
        existing = by_target.get(target_key)
        if existing is None:
            by_target[target_key] = gap
            continue
        # Prefer graph-derived / graph-cited gap for the same entity target.
        if gap.source_signal.startswith("graph_") or gap.graph_summaries:
            by_target[target_key] = gap
    return [*project_level, *by_target.values()]
