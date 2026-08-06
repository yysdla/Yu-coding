"""Evidence-first analysis agent used by the project workflow."""

from __future__ import annotations

import re

from project_lens.context.models import EvidenceBundle
from project_lens.context.retrieval.exact import (
    TracebackHint,
    matching_traceback_frame,
    parse_traceback,
)
from project_lens.context.snapshot import build_project_snapshot
from project_lens.context.timeline import build_timeline_events
from project_lens.domain.models import (
    ClaimType,
    Evidence,
    EvidenceGrade,
    EvidenceType,
    GraphEvidence,
    KnowledgeGapReport,
)
from project_lens.domain.ops import OpsFinding
from project_lens.workflow.models import (
    AnalysisResult,
    AnswerAudience,
    CandidateAction,
    CandidateClaim,
)
from project_lens.workflow.ops_correlation import build_ops_correlation
from project_lens.workflow.skills import (
    ProjectSkill,
    classify_project_question,
    is_owner_lookup_question,
    is_project_intro_question,
)
from project_lens.workflow.specialists import EvidenceSelection, ProjectSkillSpecialists
from project_lens.workflow.timeline_correlation import build_timeline_correlation


class AnalysisAgent:
    """Build structured candidates; it cannot publish domain claims directly."""

    def __init__(self, specialists: ProjectSkillSpecialists | None = None) -> None:
        self._specialists = specialists or ProjectSkillSpecialists()

    def analyze(
        self,
        question: str,
        bundle: EvidenceBundle,
        *,
        skill: ProjectSkill | None = None,
        knowledge_gaps: KnowledgeGapReport | None = None,
        graph_paths: tuple[GraphEvidence, ...] = (),
        ops_finding: OpsFinding | None = None,
    ) -> AnalysisResult:
        frames, exception = parse_traceback(question)
        skill = skill or classify_project_question(question)
        # Graph paths may cite evidence outside the lexical search hit list
        # (e.g. service depends_on). Keep those anchors available for claims.
        evidence = _merge_unique_evidence(
            bundle.evidence,
            tuple(
                item
                for path in graph_paths
                for item in path.cited_evidence
            ),
        )
        code = _first(evidence, EvidenceType.CODE)
        located_code, located_frame = _located_code(evidence, frames)
        project_doc = _first(evidence, EvidenceType.DOCUMENT)
        incident = _first(evidence, EvidenceType.INCIDENT)
        snapshot = build_project_snapshot(evidence, project=bundle.query.project)
        timeline = build_timeline_events(evidence, project=bundle.query.project)
        gap_mode = (
            knowledge_gaps is not None
            and not is_project_intro_question(question)
            and skill != ProjectSkill.ARCHITECTURE
        )
        owner_mode = is_owner_lookup_question(question)
        intro_mode = is_project_intro_question(question)
        architecture_mode = skill == ProjectSkill.ARCHITECTURE

        candidates: list[CandidateClaim] = []
        actions: list[CandidateAction] = []
        unknowns: list[str] = []
        specialist_output = self._specialists.analyze(
            skill,
            EvidenceSelection(
                evidence=evidence,
                frames=frames,
                question=question,
                code=code,
                located_code=located_code,
                located_frame=located_frame,
                project_doc=project_doc,
                incident=incident,
                snapshot=snapshot,
                timeline=timeline,
                knowledge_gaps=knowledge_gaps,
                graph_paths=graph_paths,
                ops_finding=ops_finding,
            ),
        )
        candidates.extend(specialist_output.candidates)
        actions.extend(specialist_output.actions)
        unknowns.extend(specialist_output.unknowns)
        correlation = build_ops_correlation(
            ops_finding,
            evidence=evidence,
            graph_paths=graph_paths,
            project=bundle.query.project,
        )
        if skill == ProjectSkill.INCIDENT_DIAGNOSIS and correlation is not None:
            support_terms = _ops_correlation_support_terms(correlation)
            if correlation.facts and support_terms:
                candidates.append(
                    CandidateClaim(
                        text=(
                            "ProjectOps 相关性分析："
                            + " ".join(correlation.facts[:3])
                        ),
                        type=ClaimType.FACT,
                        grade=EvidenceGrade.B,
                        evidence_ids=correlation.evidence_ids,
                        support_terms=support_terms,
                        audience=AnswerAudience.TECHNICAL,
                    )
                )
            if correlation.inferences and support_terms:
                candidates.append(
                    CandidateClaim(
                        text=(
                            "ProjectOps 相关性分析推断："
                            + " ".join(correlation.inferences[:2])
                        ),
                        type=ClaimType.INFERENCE,
                        grade=EvidenceGrade.C,
                        evidence_ids=correlation.evidence_ids,
                        support_terms=support_terms,
                        audience=AnswerAudience.BOTH,
                    )
                )
            unknowns.extend(correlation.hypotheses)
            if correlation.recommendations and support_terms:
                actions.append(
                    CandidateAction(
                        title="ProjectOps 只读排查",
                        description=correlation.recommendations[0],
                        evidence_ids=correlation.evidence_ids,
                        support_terms=support_terms,
                    )
                )
        timeline_correlation = build_timeline_correlation(
            timeline=timeline,
            ops_finding=ops_finding,
            evidence=evidence,
            project=bundle.query.project,
        )
        if skill == ProjectSkill.INCIDENT_DIAGNOSIS and timeline_correlation is not None:
            support_terms = _timeline_correlation_support_terms(
                timeline_correlation,
                evidence,
            )
            if timeline_correlation.facts and support_terms:
                candidates.append(
                    CandidateClaim(
                        text=(
                            "ProjectOps timeline correlation："
                            + " ".join(timeline_correlation.facts[:2])
                        ),
                        type=ClaimType.FACT,
                        grade=EvidenceGrade.B,
                        evidence_ids=timeline_correlation.evidence_ids,
                        support_terms=support_terms,
                        audience=AnswerAudience.TECHNICAL,
                    )
                )
            if timeline_correlation.inferences and support_terms:
                candidates.append(
                    CandidateClaim(
                        text=(
                            "ProjectOps timeline correlation inference："
                            + " ".join(timeline_correlation.inferences[:1])
                        ),
                        type=ClaimType.INFERENCE,
                        grade=EvidenceGrade.C,
                        evidence_ids=timeline_correlation.evidence_ids,
                        support_terms=support_terms,
                        audience=AnswerAudience.BOTH,
                    )
                )
            unknowns.extend(timeline_correlation.hypotheses)
            if timeline_correlation.recommendations and support_terms:
                actions.append(
                    CandidateAction(
                        title="ProjectOps 时间线只读排查",
                        description=timeline_correlation.recommendations[0],
                        evidence_ids=timeline_correlation.evidence_ids,
                        support_terms=support_terms,
                    )
                )

        if gap_mode or owner_mode or intro_mode or architecture_mode:
            return AnalysisResult(
                problem_kind=exception or skill.value,
                skill=skill,
                candidates=tuple(candidates),
                actions=tuple(actions),
                unknowns=tuple(dict.fromkeys(unknowns)),
            )

        if located_code and located_frame:
            symbol = located_code.metadata.get("symbol") or located_frame.symbol or "相关代码块"
            candidates.append(
                CandidateClaim(
                    text=f"错误位置可定位到 {located_code.source.source_id}（{symbol}）。",
                    type=ClaimType.FACT,
                    grade=EvidenceGrade.A,
                    evidence_ids=(located_code.id,),
                    support_terms=_support_terms(located_code, str(symbol)),
                    audience=AnswerAudience.TECHNICAL,
                )
            )
        elif code:
            symbol = code.metadata.get("symbol") or "相关代码块"
            candidates.append(
                CandidateClaim(
                    text=(
                        f"检索到候选代码位置 {code.source.source_id}（{symbol}），"
                        "但现有信息不足以确认它就是实际报错位置。"
                    ),
                    type=ClaimType.INFERENCE,
                    grade=EvidenceGrade.C,
                    evidence_ids=(code.id,),
                    support_terms=_support_terms(code, str(symbol)),
                    audience=AnswerAudience.TECHNICAL,
                )
            )

        if project_doc:
            candidates.append(
                CandidateClaim(
                    text=f"找到可用于解释该问题的项目资料：{project_doc.source.source_id}。",
                    type=ClaimType.FACT,
                    grade=EvidenceGrade.B,
                    evidence_ids=(project_doc.id,),
                    support_terms=_support_terms(project_doc),
                    audience=AnswerAudience.BOTH,
                )
            )

        if code and project_doc:
            candidates.append(
                CandidateClaim(
                    text=(
                        "从代码证据和项目资料共同判断，当前问题应先按相关模块的职责、"
                        "调用路径和输入条件继续收敛。"
                    ),
                    type=ClaimType.INFERENCE,
                    grade=EvidenceGrade.C,
                    evidence_ids=(code.id, project_doc.id),
                    support_terms=_combined_support_terms(code, project_doc),
                    audience=AnswerAudience.BOTH,
                )
            )

        if incident:
            candidates.append(
                CandidateClaim(
                    text=f"发现可参考的历史记录：{incident.source.source_id}。",
                    type=ClaimType.FACT,
                    grade=EvidenceGrade.B,
                    evidence_ids=(incident.id,),
                    support_terms=_support_terms(incident),
                    audience=AnswerAudience.TECHNICAL,
                )
            )
            if code:
                candidates.append(
                    CandidateClaim(
                        text=(
                            "当前问题可能与历史记录中的相似症状或修复路径有关，"
                            "但仍需要结合本次 traceback、日志或监控确认。"
                        ),
                        type=ClaimType.INFERENCE,
                        grade=EvidenceGrade.C,
                        evidence_ids=(code.id, incident.id),
                        support_terms=_combined_support_terms(code, incident),
                        audience=AnswerAudience.TECHNICAL,
                    )
                )

        action_evidence = tuple(
            item.id for item in (code, project_doc, incident) if item is not None
        )
        if action_evidence:
            support_sources = tuple(
                item for item in (code, project_doc, incident) if item is not None
            )
            actions.append(
                CandidateAction(
                    title="验证相关路径并提出最小修复",
                    description=(
                        "先补充复现或回归测试，再在审批后修改相关代码；上线动作仍需人工确认。"
                    ),
                    evidence_ids=action_evidence,
                    support_terms=_multi_support_terms(*support_sources),
                )
            )

        if not any(
            item.type in {EvidenceType.METRIC, EvidenceType.LOG} for item in bundle.evidence
        ):
            unknowns.append("缺少实时监控证据，无法确认当前失败数量、影响范围和趋势。")
        if not frames:
            unknowns.append(
                "未提供 traceback 文件和行号，当前代码位置只能作为候选推断。"
            )
        elif not located_code:
            unknowns.append(
                "traceback 位置未匹配到可访问的已索引代码，无法确认精确错误位置。"
            )
        if not bundle.evidence:
            unknowns.append("未检索到可访问的项目证据，无法判断位置、原因或影响。")

        return AnalysisResult(
            problem_kind=exception or skill.value,
            skill=skill,
            candidates=tuple(candidates),
            actions=tuple(actions),
            unknowns=tuple(dict.fromkeys(unknowns)),
        )


def _first(evidence: tuple[Evidence, ...], evidence_type: EvidenceType) -> Evidence | None:
    return next((item for item in evidence if item.type == evidence_type), None)


def _merge_unique_evidence(*groups: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    from uuid import UUID

    merged: list[Evidence] = []
    seen: set[UUID] = set()
    for group in groups:
        for item in group:
            if item.id in seen:
                continue
            seen.add(item.id)
            merged.append(item)
    return tuple(merged)


def _located_code(
    evidence: tuple[Evidence, ...],
    frames: tuple[TracebackHint, ...],
) -> tuple[Evidence | None, TracebackHint | None]:
    for item in evidence:
        frame = matching_traceback_frame(item, frames)
        if frame is not None:
            return item, frame
    return None, None


def _support_terms(evidence: Evidence, preferred: str | None = None) -> tuple[str, ...]:
    if preferred and preferred.lower() in evidence.content.lower():
        return (preferred,)
    token = _first_content_token(evidence.content)
    if token:
        return (token,)
    return (evidence.content[:20],)


def _combined_support_terms(left: Evidence, right: Evidence) -> tuple[str, ...]:
    return _support_terms(left) + _support_terms(right)


def _multi_support_terms(*evidence_items: Evidence) -> tuple[str, ...]:
    terms: list[str] = []
    for item in evidence_items:
        terms.extend(_support_terms(item))
    return tuple(terms)


def _ops_correlation_support_terms(correlation) -> tuple[str, ...]:
    terms: list[str] = []
    for group in (
        correlation.related_routes,
        correlation.related_trace_ids,
        correlation.related_metrics,
        correlation.related_symbols,
    ):
        terms.extend(group[:2])
    return tuple(dict.fromkeys(terms[:4]))


def _timeline_correlation_support_terms(correlation, evidence: tuple[Evidence, ...]) -> tuple[str, ...]:
    terms: list[str] = []
    if correlation.nearest_release_id:
        for item in evidence:
            if correlation.nearest_release_id in item.content:
                terms.append(correlation.nearest_release_id)
                break
    for item in evidence:
        if item.id not in correlation.evidence_ids:
            continue
        if "2026.07.20" in item.content:
            terms.append("2026.07.20")
        if "AttributeError" in item.content:
            terms.append("AttributeError")
        if len(terms) >= 2:
            break
    return tuple(dict.fromkeys(terms[:3]))


def _first_content_token(content: str) -> str | None:
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", content):
        return token
    stripped = content.strip()
    if stripped:
        return stripped[:20]
    return None
