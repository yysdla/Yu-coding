"""Read-only project skill specialists that produce verifiable candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from project_lens.context.retrieval.exact import TracebackHint
from project_lens.domain.models import (
    ClaimType,
    Evidence,
    EvidenceGrade,
    EvidenceType,
    GraphEvidence,
    KnowledgeGapReport,
    ProjectSnapshot,
    TimelineEvent,
)
from project_lens.domain.ops import OpsFinding
from project_lens.workflow.models import AnswerAudience, CandidateAction, CandidateClaim
from project_lens.workflow.skills import (
    ProjectSkill,
    is_owner_lookup_question,
    is_project_intro_question,
)


@dataclass(frozen=True)
class EvidenceSelection:
    evidence: tuple[Evidence, ...]
    frames: tuple[TracebackHint, ...]
    question: str = ""
    code: Evidence | None = None
    located_code: Evidence | None = None
    located_frame: TracebackHint | None = None
    project_doc: Evidence | None = None
    incident: Evidence | None = None
    snapshot: ProjectSnapshot | None = None
    timeline: tuple[TimelineEvent, ...] = ()
    knowledge_gaps: KnowledgeGapReport | None = None
    graph_paths: tuple[GraphEvidence, ...] = ()
    ops_finding: OpsFinding | None = None


@dataclass(frozen=True)
class SpecialistOutput:
    candidates: tuple[CandidateClaim, ...] = ()
    actions: tuple[CandidateAction, ...] = ()
    unknowns: tuple[str, ...] = ()


class ProjectSkillSpecialists:
    """Dispatch skill-specific read-only analysis without bypassing verification."""

    def analyze(self, skill: ProjectSkill, selection: EvidenceSelection) -> SpecialistOutput:
        if skill == ProjectSkill.ARCHITECTURE:
            return _merge_outputs(
                _architecture(selection),
                _graph_relations(selection),
            )
        if skill == ProjectSkill.VERSION_CHANGE:
            return _merge_outputs(_version_change(selection), _graph_relations(selection))
        if skill == ProjectSkill.CODE_EXPLANATION:
            return _code_explanation(selection)
        if skill == ProjectSkill.INCIDENT_DIAGNOSIS:
            return _merge_outputs(
                _incident_diagnosis(selection),
                _ops_signals(selection),
                _graph_relations(selection),
            )
        return _merge_outputs(_project_knowledge(selection), _graph_relations(selection))


def _architecture(selection: EvidenceSelection) -> SpecialistOutput:
    """Build a navigable project map: services, entrypoints, deps, owners, changes, gaps."""

    return _merge_outputs(
        _project_map_overview(selection),
        _intro_services(selection),
        _intro_entrypoints(selection),
        _map_dependencies(selection),
        _owner_lookup(selection),
        _intro_recent_changes(selection),
        _intro_risks_and_gaps(selection),
        _architecture_doc(selection),
    )


def _architecture_doc(selection: EvidenceSelection) -> SpecialistOutput:
    doc = _best_for_terms(
        selection.evidence,
        ("architecture", "order service", "payment service", "create_order"),
    )
    if doc is not None and doc.type != EvidenceType.DOCUMENT:
        doc = selection.project_doc
    doc = doc or selection.project_doc
    if doc is None:
        return SpecialistOutput(unknowns=("缺少架构资料，无法说明模块、服务或依赖关系。",))
    phrases = _present_phrases(
        doc,
        ("order service", "payment service", "Coupons", "create_order"),
    )
    summary = "、".join(phrases) if phrases else doc.source.source_id
    terms = phrases[:2] or _support_terms(doc)
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=f"项目架构资料 {doc.source.source_id} 明确提到：{summary}。",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(doc.id,),
                support_terms=tuple(terms),
                audience=AnswerAudience.BOTH,
            ),
        ),
        actions=(
            CandidateAction(
                title="按入口函数和上下游服务继续梳理项目地图",
                description="优先补齐入口、依赖服务、可选字段边界和影响面，形成可追问的项目地图。",
                evidence_ids=(doc.id,),
                support_terms=tuple(terms[:1]),
            ),
        ),
    )


def _project_map_overview(selection: EvidenceSelection) -> SpecialistOutput:
    snapshot = selection.snapshot
    if snapshot is None or not snapshot.evidence_ids:
        return SpecialistOutput()
    terms = _snapshot_support_terms(selection, snapshot)
    if not terms:
        return SpecialistOutput(
            unknowns=("项目快照缺少可验证的支撑词，暂不输出项目地图结论。",)
        )
    parts = []
    if snapshot.services:
        parts.append("服务：" + "、".join(snapshot.services[:3]))
    if snapshot.entrypoints:
        parts.append("入口：" + "、".join(snapshot.entrypoints[:3]))
    if snapshot.dependencies:
        parts.append("依赖：" + "、".join(snapshot.dependencies[:3]))
    if snapshot.risks:
        parts.append("风险：" + "、".join(snapshot.risks[:3]))
    if not parts:
        return SpecialistOutput(unknowns=snapshot.unresolved_items)
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text="项目地图：" + "；".join(parts) + "。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=snapshot.evidence_ids,
                support_terms=terms,
                audience=AnswerAudience.BOTH,
            ),
        ),
        actions=(
            CandidateAction(
                title="把项目地图沉淀为可维护快照",
                description="将服务、入口、依赖、风险和证据来源作为项目快照维护，后续接入任务、发布和负责人信息。",
                evidence_ids=snapshot.evidence_ids,
                support_terms=terms[:1],
            ),
        ),
    )


def _map_dependencies(selection: EvidenceSelection) -> SpecialistOutput:
    snapshot = selection.snapshot
    if snapshot is None or not snapshot.dependencies or not snapshot.evidence_ids:
        return SpecialistOutput(unknowns=("缺少可验证的上下游依赖证据。",))
    terms = _snapshot_support_terms(selection, snapshot)
    dep_terms = tuple(
        item for item in snapshot.dependencies if item.lower() in " ".join(terms).lower()
    ) or terms
    if not dep_terms:
        return SpecialistOutput(unknowns=("上下游依赖缺少可验证的支撑词，暂不输出依赖清单。",))
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text="上下游依赖：" + "、".join(snapshot.dependencies[:5]) + "。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=snapshot.evidence_ids,
                support_terms=dep_terms[:3],
                audience=AnswerAudience.BOTH,
            ),
        ),
    )


def _version_change(selection: EvidenceSelection) -> SpecialistOutput:
    timeline_output = _version_timeline(selection)
    evidence = _best_for_terms(
        selection.evidence,
        ("latest release", "release", "changed", "refactor", "commit", "pull request"),
    )
    if evidence is None:
        return SpecialistOutput(
            candidates=timeline_output.candidates,
            actions=timeline_output.actions,
            unknowns=timeline_output.unknowns
            or ("缺少版本、发布或提交证据，无法判断这次变更改了什么。",),
    )
    phrases = _present_phrases(
        evidence,
        ("latest release", "release", "changed", "refactor", "commit", "pull request"),
    )
    terms = phrases[:2] or _support_terms(evidence)
    impact_output = _version_impact(selection, evidence)
    return SpecialistOutput(
        candidates=timeline_output.candidates
        + (
            CandidateClaim(
                text=f"版本或变更相关证据 {evidence.source.source_id} 提到：{'、'.join(terms)}。",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(evidence.id,),
                support_terms=tuple(terms),
                audience=AnswerAudience.BOTH,
            ),
        )
        + impact_output.candidates,
        actions=timeline_output.actions
        + (
            CandidateAction(
                title="按时间线核对版本变更",
                description="按发布记录、相关任务和回归测试确认变更影响，暂不直接执行上线或回滚动作。",
                evidence_ids=(evidence.id,),
                support_terms=tuple(terms[:1]),
            ),
        )
        + impact_output.actions,
        unknowns=timeline_output.unknowns,
    )


def _version_timeline(selection: EvidenceSelection) -> SpecialistOutput:
    events = tuple(
        event
        for event in selection.timeline
        if event.event_type
        in {"commit", "pull_request", "incident", "task", "release", "document"}
    )
    if not events:
        return SpecialistOutput(unknowns=("缺少可排序的项目时间线事件。",))
    evidence_ids = tuple(event.evidence_id for event in events[:5])
    support_terms = _timeline_support_terms(selection, events[:5])
    if not support_terms:
        return SpecialistOutput(unknowns=("项目时间线缺少可验证的支撑词，暂不输出变更时间线。",))
    summary = "；".join(
        f"{event.observed_at.date().isoformat()} {event.event_type}: {event.title}"
        for event in events[:3]
    )
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=f"项目时间线显示：{summary}。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=evidence_ids,
                support_terms=support_terms,
                audience=AnswerAudience.BOTH,
            ),
        ),
        actions=(
            CandidateAction(
                title="沿时间线核对发布、任务和事故",
                description="先对齐提交、发布、任务和事故发生时间，再判断版本变更与故障之间是否存在因果关系。",
                evidence_ids=evidence_ids,
                support_terms=support_terms[:1],
            ),
        ),
    )


def _version_impact(selection: EvidenceSelection, change: Evidence) -> SpecialistOutput:
    incident = _timeline_evidence(selection, {"incident"})
    if incident is None:
        return SpecialistOutput()
    support_terms = _impact_support_terms(change, incident, selection.snapshot)
    if not support_terms:
        return SpecialistOutput()
    snapshot = selection.snapshot
    if snapshot is not None and snapshot.risks:
        risk_summary = "、".join(snapshot.risks[:2])
        claim_text = (
            f"项目快照中的风险项 {risk_summary} 与时间线中的提交和事故一致，"
            "说明这次变更可能影响了相关 checkout / coupon 路径。"
        )
    else:
        claim_text = "时间线中的提交和事故一致，说明这次变更可能影响了 checkout / coupon 路径。"
    evidence_ids = tuple(dict.fromkeys((change.id, incident.id)))
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=claim_text,
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=evidence_ids,
                support_terms=support_terms,
                audience=AnswerAudience.BOTH,
            ),
        ),
        actions=(
            CandidateAction(
                title="优先回看 checkout / coupon 回归",
                description="结合提交、事故和项目快照中的风险项，先核对无 coupon 场景的回归测试，再决定是否需要修复或回滚。",
                evidence_ids=evidence_ids,
                support_terms=support_terms[:1],
            ),
        ),
    )


def _code_explanation(selection: EvidenceSelection) -> SpecialistOutput:
    code = selection.located_code or selection.code
    if code is None:
        return SpecialistOutput(unknowns=("缺少可访问的代码证据，无法解释函数、方法或实现细节。",))
    symbol = code.metadata.get("symbol")
    symbol_type = code.metadata.get("symbol_type") or "code"
    preferred_terms = tuple(str(item) for item in (symbol, symbol_type) if item)
    terms = _present_phrases(code, preferred_terms) or _support_terms(code)
    if symbol:
        text = f"代码实现证据 {code.source.source_id} 包含 {symbol_type} {symbol}。"
    else:
        text = f"代码实现证据 {code.source.source_id} 可用于解释当前实现。"
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=text,
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(code.id,),
                support_terms=tuple(terms[:1]),
                audience=AnswerAudience.TECHNICAL,
            ),
        ),
        actions=(
            CandidateAction(
                title="按代码路径解释输入、处理和返回值",
                description="先基于只读证据解释实现；如需修改，后续必须进入 Explain -> Propose -> Validate -> Apply。",
                evidence_ids=(code.id,),
                support_terms=tuple(terms[:1]),
            ),
        ),
    )


def _incident_diagnosis(selection: EvidenceSelection) -> SpecialistOutput:
    incident = selection.incident
    if incident is None:
        return SpecialistOutput(unknowns=("缺少历史事故记录，无法与既往影响、原因或处理方式对照。",))
    terms = _present_phrases(
        incident,
        ("HTTP 500", "root_cause", "resolution", "impact", "Checkout", "coupon"),
    ) or _support_terms(incident)
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=f"历史事故 {incident.source.source_id} 可用于对照本次故障的影响、原因和处理方式。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=(incident.id,),
                support_terms=tuple(terms[:1]),
                audience=AnswerAudience.TECHNICAL,
            ),
        ),
        actions=(
            CandidateAction(
                title="先确认本次故障是否复现历史症状",
                description="对照历史事故、traceback、日志和监控，确认影响范围后再提出最小修复。",
                evidence_ids=(incident.id,),
                support_terms=tuple(terms[:1]),
            ),
        ),
    )


def _ops_signals(selection: EvidenceSelection) -> SpecialistOutput:
    finding = selection.ops_finding
    if finding is None or not finding.evidence:
        return SpecialistOutput()
    candidates: list[CandidateClaim] = []
    for item in finding.evidence[:4]:
        kind = str(item.metadata.get("ops_kind") or item.type.value)
        terms = _present_phrases(
            item,
            ("checkout", "coupon", "create_order", "http_5xx_rate", "AttributeError", "trace_id"),
        ) or _support_terms(item)
        if not terms:
            continue
        summary = _ops_summary_text(item)
        candidates.append(
            CandidateClaim(
                text=f"运维时间窗信号（{kind}）确认：{summary}",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(item.id,),
                support_terms=tuple(terms[:2]),
                audience=AnswerAudience.TECHNICAL,
            )
        )
    actions: tuple[CandidateAction, ...] = ()
    if finding.evidence:
        anchor = finding.evidence[0]
        terms = _support_terms(anchor)
        actions = (
            CandidateAction(
                title="对照日志/指标/trace 收敛影响范围",
                description=(
                    f"{finding.summary} 运维信号仅用于当前时间窗只读分析，"
                    "不会写入长期知识库；确认后可沉淀为事故复盘。"
                ),
                evidence_ids=(anchor.id,),
                support_terms=terms[:1],
            ),
        )
    return SpecialistOutput(candidates=tuple(candidates), actions=actions)


def _ops_summary_text(item: Evidence) -> str:
    for line in item.content.splitlines():
        if line.startswith("summary:"):
            return line.split(":", 1)[1].strip()[:300]
    return item.content.strip()[:300]


def _project_knowledge(selection: EvidenceSelection) -> SpecialistOutput:
    if is_project_intro_question(selection.question):
        return _project_intro(selection)
    if selection.knowledge_gaps is not None:
        return _knowledge_gaps(selection.knowledge_gaps, selection.evidence)
    if is_owner_lookup_question(selection.question):
        return _owner_lookup(selection)
    evidence = selection.project_doc or selection.code or selection.incident
    if evidence is None:
        return SpecialistOutput(unknowns=("缺少可访问的项目资料，无法回答项目知识类问题。",))
    terms = _support_terms(evidence)
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=f"项目问题证据 {evidence.source.source_id} 可作为回答该问题的依据。",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(evidence.id,),
                support_terms=terms,
                audience=AnswerAudience.BOTH,
            ),
        ),
    )


def _project_intro(selection: EvidenceSelection) -> SpecialistOutput:
    """Compose a read-only project introduction from Evidence / snapshot / timeline / gaps."""

    outputs: list[SpecialistOutput] = [
        _intro_positioning(selection),
        _intro_services(selection),
        _intro_entrypoints(selection),
        _intro_documents(selection),
        _owner_lookup(selection),
        _intro_recent_changes(selection),
        _intro_risks_and_gaps(selection),
    ]
    return _merge_outputs(*outputs)


def _intro_positioning(selection: EvidenceSelection) -> SpecialistOutput:
    doc = selection.project_doc
    if doc is None:
        return SpecialistOutput(unknowns=("缺少架构/背景文档，暂不能确认项目定位。",))
    phrases = _present_phrases(
        doc,
        ("order service", "payment service", "checkout", "architecture", "create_order"),
    )
    terms = phrases[:2] or _support_terms(doc)
    summary = "、".join(phrases[:3]) if phrases else doc.content.strip().splitlines()[0][:120]
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=f"项目定位：依据 {doc.source.source_id}，项目涉及 {summary}。",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(doc.id,),
                support_terms=tuple(terms),
                audience=AnswerAudience.BOTH,
            ),
        ),
    )


def _intro_services(selection: EvidenceSelection) -> SpecialistOutput:
    snapshot = selection.snapshot
    if snapshot is None or not snapshot.services or not snapshot.evidence_ids:
        return SpecialistOutput(unknowns=("缺少可验证的核心服务证据。",))
    terms = _snapshot_support_terms(selection, snapshot)
    service_terms = tuple(
        item for item in snapshot.services if item.lower() in " ".join(terms).lower()
    ) or terms
    if not service_terms:
        return SpecialistOutput(unknowns=("核心服务缺少可验证的支撑词，暂不输出服务清单。",))
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text="核心服务：" + "、".join(snapshot.services[:5]) + "。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=snapshot.evidence_ids,
                support_terms=service_terms[:3],
                audience=AnswerAudience.BOTH,
            ),
        ),
    )


def _intro_entrypoints(selection: EvidenceSelection) -> SpecialistOutput:
    snapshot = selection.snapshot
    if snapshot is None or not snapshot.entrypoints or not snapshot.evidence_ids:
        return SpecialistOutput(unknowns=("缺少可验证的关键入口证据。",))
    terms = _snapshot_support_terms(selection, snapshot)
    entry_terms = tuple(
        item for item in snapshot.entrypoints if item.lower() in " ".join(terms).lower()
    ) or terms
    if not entry_terms:
        return SpecialistOutput(unknowns=("关键入口缺少可验证的支撑词，暂不输出入口清单。",))
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text="关键入口：" + "、".join(snapshot.entrypoints[:5]) + "。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=snapshot.evidence_ids,
                support_terms=entry_terms[:3],
                audience=AnswerAudience.BOTH,
            ),
        ),
    )


def _intro_documents(selection: EvidenceSelection) -> SpecialistOutput:
    docs = tuple(item for item in selection.evidence if item.type == EvidenceType.DOCUMENT)
    if not docs:
        return SpecialistOutput(unknowns=("缺少主要文档证据。",))
    candidates: list[CandidateClaim] = []
    for item in docs[:4]:
        terms = _support_terms(item)
        if not terms:
            continue
        candidates.append(
            CandidateClaim(
                text=f"主要文档：{item.source.source_id}。",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(item.id,),
                support_terms=terms,
                audience=AnswerAudience.BOTH,
            )
        )
    if not candidates:
        return SpecialistOutput(unknowns=("主要文档缺少可验证的支撑词。",))
    return SpecialistOutput(candidates=tuple(candidates))


def _intro_recent_changes(selection: EvidenceSelection) -> SpecialistOutput:
    events = tuple(
        event
        for event in selection.timeline
        if event.event_type in {"commit", "pull_request", "release", "document", "task"}
    )
    if not events:
        return SpecialistOutput(unknowns=("缺少最近变更时间线事件。",))
    support_terms = _timeline_support_terms(selection, events[:5])
    if not support_terms:
        return SpecialistOutput(unknowns=("最近变更缺少可验证的支撑词。",))
    summary = "；".join(
        f"{event.observed_at.date().isoformat()} {event.event_type}: {event.title}"
        for event in events[:3]
    )
    return SpecialistOutput(
        candidates=(
            CandidateClaim(
                text=f"最近变更：{summary}。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=tuple(event.evidence_id for event in events[:5]),
                support_terms=support_terms,
                audience=AnswerAudience.BOTH,
            ),
        ),
    )


def _intro_risks_and_gaps(selection: EvidenceSelection) -> SpecialistOutput:
    candidates: list[CandidateClaim] = []
    unknowns: list[str] = []
    actions: list[CandidateAction] = []
    snapshot = selection.snapshot
    if snapshot is not None and snapshot.risks and snapshot.evidence_ids:
        terms = _snapshot_support_terms(selection, snapshot)
        risk_terms = tuple(
            item for item in snapshot.risks if item.lower() in " ".join(terms).lower()
        ) or terms
        if risk_terms:
            candidates.append(
                CandidateClaim(
                    text="风险信号：" + "、".join(snapshot.risks[:3]) + "。",
                    type=ClaimType.INFERENCE,
                    grade=EvidenceGrade.C,
                    evidence_ids=snapshot.evidence_ids,
                    support_terms=risk_terms[:3],
                    audience=AnswerAudience.BOTH,
                )
            )
    if snapshot is not None and snapshot.unresolved_items:
        unknowns.extend(f"知识缺口：{item}" for item in snapshot.unresolved_items[:4])
    if selection.knowledge_gaps is not None:
        gap_output = _knowledge_gaps(selection.knowledge_gaps, selection.evidence)
        # Keep gap coverage claim + top unknowns/actions; avoid drowning the intro.
        candidates.extend(gap_output.candidates[:1])
        unknowns.extend(gap_output.unknowns[:6])
        actions.extend(gap_output.actions[:3])
    if not candidates and not unknowns:
        unknowns.append("当前 ACL 范围内缺少可展示的风险或知识缺口结论。")
    return SpecialistOutput(
        candidates=tuple(candidates),
        actions=tuple(actions),
        unknowns=tuple(dict.fromkeys(unknowns)),
    )


def _graph_relations(selection: EvidenceSelection) -> SpecialistOutput:
    """Turn ContextEngine graph paths into verifiable relation claims."""

    if not selection.graph_paths:
        return SpecialistOutput()
    evidence_by_id = {item.id: item for item in selection.evidence}
    candidates: list[CandidateClaim] = []
    seen: set[str] = set()
    for path in selection.graph_paths[:8]:
        if path.summary in seen:
            continue
        cited = tuple(
            evidence_by_id[item_id]
            for item_id in path.evidence_ids
            if item_id in evidence_by_id
        )
        if not cited:
            continue
        terms = _graph_support_terms(cited, path)
        if not terms:
            continue
        relation_text = " / ".join(path.relations) if path.relations else "related"
        seen.add(path.summary)
        candidates.append(
            CandidateClaim(
                text=f"项目关系图显示：{path.summary}（{relation_text}）。",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=tuple(item.id for item in cited),
                support_terms=terms,
                audience=AnswerAudience.BOTH,
            )
        )
    return SpecialistOutput(candidates=tuple(candidates))


def _graph_support_terms(
    cited: tuple[Evidence, ...],
    path: GraphEvidence,
) -> tuple[str, ...]:
    content = "\n".join(item.content for item in cited).lower()
    terms: list[str] = []
    for label in path.path_labels:
        if label and label.lower() in content:
            terms.append(label)
    for item in cited:
        for term in _support_terms(item):
            if term.lower() in content:
                terms.append(term)
                break
    return tuple(dict.fromkeys(terms[:3]))


def _merge_outputs(*outputs: SpecialistOutput) -> SpecialistOutput:
    candidates: list[CandidateClaim] = []
    actions: list[CandidateAction] = []
    unknowns: list[str] = []
    for output in outputs:
        candidates.extend(output.candidates)
        actions.extend(output.actions)
        unknowns.extend(output.unknowns)
    return SpecialistOutput(
        candidates=tuple(candidates),
        actions=tuple(actions),
        unknowns=tuple(dict.fromkeys(unknowns)),
    )


def _owner_lookup(selection: EvidenceSelection) -> SpecialistOutput:
    """Build owner facts from ACL-filtered evidence metadata and content."""

    candidates: list[CandidateClaim] = []
    seen: set[str] = set()
    for item in selection.evidence:
        owner_name = _owner_name_from_content(item)
        owner_id = item.metadata.get("owner_user_id") or item.metadata.get("owner")
        if owner_name is None and owner_id is None:
            continue
        label = owner_name or str(owner_id)
        key = f"{item.project.service or 'project'}:{label}"
        if key in seen:
            continue
        seen.add(key)
        service = item.project.service or "该项目"
        if owner_name and owner_id and str(owner_id).lower() != owner_name.lower():
            text = (
                f"{service} 负责人可从 {item.source.source_id} 确认为 "
                f"{owner_name}（owner={owner_id}）。"
            )
        else:
            text = f"{service} 负责人可从 {item.source.source_id} 确认为 {label}。"
        terms = _owner_support_terms(item, owner_name=owner_name, owner_id=owner_id)
        if not terms:
            continue
        candidates.append(
            CandidateClaim(
                text=text,
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(item.id,),
                support_terms=terms,
                audience=AnswerAudience.BOTH,
            )
        )
        if len(candidates) >= 5:
            break
    if candidates:
        return SpecialistOutput(candidates=tuple(candidates))
    return SpecialistOutput(
        unknowns=("当前 ACL 范围内缺少负责人 / owner 证据，无法确认模块或服务负责人。",),
    )


_OWNER_CONTENT_RE = re.compile(
    r"(?:owner|负责人)\s*(?:is|是|:|：)\s*([A-Za-z0-9_\-.]+)",
    re.IGNORECASE,
)


def _owner_name_from_content(evidence: Evidence) -> str | None:
    match = _OWNER_CONTENT_RE.search(evidence.content)
    if match is None:
        return None
    return match.group(1)


def _owner_support_terms(
    evidence: Evidence,
    *,
    owner_name: str | None,
    owner_id: object | None,
) -> tuple[str, ...]:
    content = evidence.content.lower()
    terms: list[str] = []
    if owner_name and owner_name.lower() in content:
        terms.append(owner_name)
    if "owner" in content:
        terms.append("owner")
    elif "负责人" in evidence.content:
        terms.append("负责人")
    owner_id_text = str(owner_id) if owner_id is not None else ""
    if owner_id_text and owner_id_text.lower() in content:
        terms.append(owner_id_text)
    if not terms:
        return _support_terms(evidence)
    return tuple(dict.fromkeys(terms[:3]))


def _knowledge_gaps(
    report: KnowledgeGapReport,
    evidence: tuple[Evidence, ...],
) -> SpecialistOutput:
    candidates: list[CandidateClaim] = []
    actions: list[CandidateAction] = []
    unknowns: list[str] = []
    covered = ", ".join(
        f"{kind}={count}" for kind, count in sorted(report.type_coverage.items())
    ) or "无"
    signals = ", ".join(
        f"{kind}={count}" for kind, count in sorted(report.signal_coverage.items())
    ) or "无"
    evidence_by_id = {item.id: item for item in evidence}
    anchor = evidence[0] if evidence else None
    if anchor is not None:
        terms = _support_terms(anchor)
        candidates.append(
            CandidateClaim(
                text=f"当前可访问证据类型覆盖：{covered}；主题覆盖：{signals}。",
                type=ClaimType.FACT,
                grade=EvidenceGrade.B,
                evidence_ids=(anchor.id,),
                support_terms=terms,
                audience=AnswerAudience.BOTH,
            )
        )
    for gap in report.gaps:
        owners = "、".join(gap.suggested_owners[:3])
        target = gap.target_ref or "project"
        cited = tuple(item_id for item_id in gap.evidence_ids if item_id in evidence_by_id)
        if not cited and gap.evidence_ids:
            cited = gap.evidence_ids[:3]
        if not cited and anchor is not None:
            cited = (anchor.id,)
        detail = (
            f"[{gap.type.value}|{gap.severity.value}] "
            f"target={target} | {gap.title}：{gap.description}"
        )
        if owners:
            detail = f"{detail} | 建议谁补：{owners}"
        detail = f"{detail} | 为何判断：{gap.source_signal}"
        if cited:
            detail = f"{detail} | 证据：{', '.join(str(item_id) for item_id in cited[:3])}"
        if gap.graph_summaries:
            detail = f"{detail} | 图谱：{gap.graph_summaries[0]}"
        unknowns.append(detail)
        support_source = next(
            (evidence_by_id[item_id] for item_id in cited if item_id in evidence_by_id),
            anchor,
        )
        if gap.recommendation and support_source is not None:
            action_desc = f"{gap.recommendation}（signal={gap.source_signal}）"
            if owners:
                action_desc = f"{action_desc}；建议谁补：{owners}"
            actions.append(
                CandidateAction(
                    title=f"补充：{gap.title}",
                    description=action_desc,
                    evidence_ids=cited or (support_source.id,),
                    support_terms=_support_terms(support_source),
                )
            )
        elif gap.recommendation:
            # Keep recommendation visible even when no evidence exists to cite.
            suggest = f"建议：{gap.recommendation}（signal={gap.source_signal}）"
            if owners:
                suggest = f"{suggest}；建议谁补：{owners}"
            unknowns.append(suggest)
    if not report.gaps and anchor is not None:
        candidates.append(
            CandidateClaim(
                text="在当前 ACL 范围内未发现明确知识库缺口。",
                type=ClaimType.INFERENCE,
                grade=EvidenceGrade.C,
                evidence_ids=(anchor.id,),
                support_terms=_support_terms(anchor),
                audience=AnswerAudience.BOTH,
            )
        )
    if not evidence:
        unknowns.append("缺少可访问的项目资料，无法评估知识库缺口。")
    return SpecialistOutput(
        candidates=tuple(candidates),
        actions=tuple(actions[:8]),
        unknowns=tuple(dict.fromkeys(unknowns)),
    )


def _best_for_terms(
    evidence: tuple[Evidence, ...],
    terms: tuple[str, ...],
) -> Evidence | None:
    for item in evidence:
        if item.type in {EvidenceType.COMMIT, EvidenceType.PULL_REQUEST}:
            return item
    for item in evidence:
        if _present_phrases(item, terms):
            return item
    return None


def _present_phrases(evidence: Evidence, phrases: tuple[str, ...]) -> tuple[str, ...]:
    content = evidence.content.lower()
    return tuple(phrase for phrase in phrases if phrase.lower() in content)


def _support_terms(evidence: Evidence) -> tuple[str, ...]:
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", evidence.content):
        return (token,)
    stripped = evidence.content.strip()
    if stripped:
        return (stripped[:20],)
    return (evidence.source.source_id,)


def _snapshot_support_terms(
    selection: EvidenceSelection,
    snapshot: ProjectSnapshot,
) -> tuple[str, ...]:
    content = "\n".join(item.content for item in selection.evidence).lower()
    candidates = (
        snapshot.services
        + snapshot.entrypoints
        + snapshot.dependencies
        + snapshot.risks
    )
    terms = [item for item in candidates if item.lower() in content]
    return tuple(dict.fromkeys(terms[:3]))


def _timeline_support_terms(
    selection: EvidenceSelection,
    events: tuple[TimelineEvent, ...],
) -> tuple[str, ...]:
    evidence_by_id = {item.id: item for item in selection.evidence}
    terms: list[str] = []
    for event in events:
        evidence = evidence_by_id.get(event.evidence_id)
        if evidence is None:
            continue
        for candidate in (event.title, event.event_type, *_support_terms(evidence)):
            if candidate and candidate.lower() in evidence.content.lower():
                terms.append(candidate)
                break
    return tuple(dict.fromkeys(terms[:3]))


def _timeline_evidence(
    selection: EvidenceSelection, event_types: set[str]
) -> Evidence | None:
    evidence_by_id = {item.id: item for item in selection.evidence}
    for event in selection.timeline:
        if event.event_type not in event_types:
            continue
        evidence = evidence_by_id.get(event.evidence_id)
        if evidence is not None:
            return evidence
    return None


def _impact_support_terms(
    change: Evidence,
    incident: Evidence,
    snapshot: ProjectSnapshot | None,
) -> tuple[str, ...]:
    preferred = (
        "checkout",
        "coupon",
        "null guard",
        "HTTP 500",
        "impact",
        "regression",
        "release",
    )
    terms: list[str] = []
    terms.extend(_present_phrases(change, preferred))
    terms.extend(_present_phrases(incident, preferred))
    if snapshot is not None:
        combined_content = f"{change.content}\n{incident.content}".lower()
        for risk in snapshot.risks:
            if risk.lower() in combined_content:
                terms.append(risk)
    return tuple(dict.fromkeys(terms[:4]))
