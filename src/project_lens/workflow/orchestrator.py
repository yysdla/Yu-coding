"""Deterministic first vertical workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from project_lens.context.engine import ContextEngine
from project_lens.context.knowledge_gaps import is_knowledge_gap_question
from project_lens.context.models import AccessContext, ContextQuery, EvidenceBundle, RetrievalHit
from project_lens.domain.conversation import ConversationSession
from project_lens.domain.memory import ProjectMemory
from project_lens.domain.models import (
    AgentRun,
    Evidence,
    GraphEvidence,
    ProjectAnswer,
    ProjectRef,
    RunStatus,
)
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.runtime.read_gateway import ReadContextGateway
from project_lens.workflow.analysis import AnalysisAgent
from project_lens.workflow.composer import AnswerComposer
from project_lens.workflow.context_pack import (
    ContextPack,
    ContextProvenance,
    TaskScratchpad,
    build_context_pack,
)
from project_lens.workflow.context_prompt import ContextPrompt, render_context_prompt
from project_lens.workflow.engineering_bridge import (
    engineering_action_from_answer,
    maybe_attach_engineering_proposal,
)
from project_lens.workflow.expression_enhancer import maybe_enhance_answer
from project_lens.workflow.engineering_skill import ProjectEngineeringSkill
from project_lens.workflow.graph_hints import graph_queries_for_question
from project_lens.workflow.model_adapter import (
    ModelAdapter,
    ModelAdapterResult,
    failed_adapter_result,
)
from project_lens.workflow.providers.factory import create_default_model_adapter
from project_lens.workflow.ops_bridge import maybe_collect_ops_finding
from project_lens.workflow.resolver import ProjectResolver
from project_lens.workflow.skills import (
    ProjectSkill,
    classify_project_question,
    is_project_intro_question,
)
from project_lens.workflow.task_scratchpad import (
    merge_scratchpads,
    scratchpad_from_answer,
    scratchpad_from_ops_finding,
)
from project_lens.workflow.verification import VerificationAgent

TransitionHandler = Callable[[RunStatus, dict[str, object]], Awaitable[None]]
MAX_GRAPH_PATHS_PER_RUN = 8


class ProjectWorkflow:
    def __init__(
        self,
        resolver: ProjectResolver,
        context_engine: ContextEngine,
        *,
        analyzer: AnalysisAgent | None = None,
        verifier: VerificationAgent | None = None,
        composer: AnswerComposer | None = None,
        engineering_skill: ProjectEngineeringSkill | None = None,
        lifecycle: LifecycleBus | None = None,
        model_adapter: ModelAdapter | None = None,
        read_gateway: ReadContextGateway | None = None,
    ) -> None:
        self._resolver = resolver
        self._context_engine = context_engine
        self._read_gateway = read_gateway or ReadContextGateway(context_engine)
        self._analyzer = analyzer or AnalysisAgent()
        self._verifier = verifier or VerificationAgent()
        self._composer = composer or AnswerComposer()
        self._engineering_skill = engineering_skill
        self._lifecycle = lifecycle or LifecycleBus()
        self._model_adapter = model_adapter or create_default_model_adapter()
        self._last_context_pack: ContextPack | None = None
        self._last_context_prompt: ContextPrompt | None = None
        self._last_model_adapter_result: ModelAdapterResult | None = None

    @property
    def last_context_pack(self) -> ContextPack | None:
        return self._last_context_pack

    @property
    def last_context_prompt(self) -> ContextPrompt | None:
        return self._last_context_prompt

    @property
    def last_model_adapter_result(self) -> ModelAdapterResult | None:
        return self._last_model_adapter_result

    @property
    def read_gateway(self) -> ReadContextGateway:
        return self._read_gateway

    def set_last_task_state(self, task_state: TaskScratchpad) -> None:
        if self._last_context_pack is None:
            return
        self._last_context_pack = self._last_context_pack.model_copy(
            update={"task_state": task_state}
        )

    async def execute(
        self,
        run: AgentRun,
        on_transition: TransitionHandler,
        *,
        session: ConversationSession | None = None,
        memories: tuple[ProjectMemory, ...] = (),
    ) -> ProjectAnswer:
        skill = classify_project_question(run.question)
        await on_transition(RunStatus.RESOLVING, {})
        resolved = self._resolver.resolve(run.project)
        await self._lifecycle.emit_async(
            LifecycleEventType.RUN_RESOLVED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=resolved.project,
            payload={"resolution": resolved.resolution, "skill": skill.value},
        )

        await on_transition(
            RunStatus.COLLECTING, {"resolution": resolved.resolution}
        )
        access = AccessContext(
            tenant_id=resolved.project.tenant_id,
            user_id=run.user_id,
            permissions=frozenset({resolved.access_scope}),
        )
        # Fresh per-run audit trail for read tools.
        self._read_gateway.reset_audit()
        knowledge_gaps = None
        # Intro / map / gap questions need broader authorized evidence than lexical search.
        use_authorized = (
            is_knowledge_gap_question(run.question)
            or is_project_intro_question(run.question)
            or skill == ProjectSkill.ARCHITECTURE
        )
        evidence_limit = 50 if use_authorized else 10
        if use_authorized:
            # Gap detection stays in ContextEngine; workflow only consumes via gateway.
            knowledge_gaps = self._read_gateway.list_knowledge_gaps(
                resolved.project, access
            )
            authorized = self._read_gateway.authorized_evidence(
                resolved.project,
                access,
                limit=evidence_limit,
            )
            bundle = _bundle_from_authorized(
                authorized,
                project=resolved.project,
                question=run.question,
            )
        else:
            bundle = self._read_gateway.search_context(
                ContextQuery(
                    text=run.question,
                    project=resolved.project,
                    limit=evidence_limit,
                ),
                access,
            )

        graph_paths, bundle = _collect_graph_paths(
            self._read_gateway,
            project=resolved.project,
            access=access,
            skill=skill,
            question=run.question,
            bundle=bundle,
        )
        ops_finding, bundle = maybe_collect_ops_finding(
            self._read_gateway,
            project=resolved.project,
            access=access,
            skill=skill,
            question=run.question,
            bundle=bundle,
        )
        if ops_finding is not None and ops_finding.signals:
            await self._lifecycle.emit_async(
                LifecycleEventType.OPS_SIGNAL_QUERIED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=resolved.project,
                payload={
                    "signal_count": len(ops_finding.signals),
                    "ephemeral": True,
                },
            )

        provenance = ContextProvenance(
            retrieval_mode="authorized" if use_authorized else "search",
            evidence_source="read_context_gateway",
            memory_source="approved_project_memory",
            graph_path_count=len(graph_paths),
            ops_signal_count=len(ops_finding.signals) if ops_finding else 0,
            knowledge_gap_count=len(knowledge_gaps.gaps) if knowledge_gaps else 0,
            evidence_limit=evidence_limit,
        )
        pack = build_context_pack(
            run=run,
            resolved=resolved,
            access=access,
            session=session,
            evidence=bundle.evidence,
            memories=memories,
            skill=skill.value,
            allow_apply=False,
            provenance=provenance,
        )
        self._last_context_pack = pack
        # Sole model-context entry: ContextPack -> ContextPrompt -> ModelAdapter/Provider.
        prompt = render_context_prompt(pack)
        self._last_context_prompt = prompt
        try:
            adapter_result = await self._model_adapter.prepare(prompt)
        except Exception as exc:  # noqa: BLE001 - never crash Feishu/run on provider failure
            adapter_result = failed_adapter_result(error=str(exc), prompt=prompt)
            await self._lifecycle.emit_async(
                LifecycleEventType.MODEL_PROVIDER_FAILED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=resolved.project,
                payload={
                    "error": str(exc)[:500],
                    "model_adapter": adapter_result.audit_refs(),
                },
            )
        self._last_model_adapter_result = adapter_result
        if adapter_result.status in {"error", "timeout"} and not adapter_result.fallback_used:
            await self._lifecycle.emit_async(
                LifecycleEventType.MODEL_PROVIDER_FAILED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=resolved.project,
                payload={"model_adapter": adapter_result.audit_refs()},
            )
        await self._lifecycle.emit_async(
            LifecycleEventType.CONTEXT_COLLECTED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=resolved.project,
            payload={
                "evidence_count": len(bundle.evidence),
                "graph_path_count": len(graph_paths),
                "memory_count": len(memories),
                "provenance": provenance.model_dump(),
                "read_gateway": self._read_gateway.audit_summary(),
            },
        )
        await self._lifecycle.emit_async(
            LifecycleEventType.CONTEXT_PROMPT_RENDERED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=resolved.project,
            payload={
                "context_prompt": prompt.audit_refs,
                "model_adapter": adapter_result.audit_refs(),
            },
        )

        await on_transition(
            RunStatus.ANALYZING,
            {
                "evidence_count": len(bundle.evidence),
                "warnings": list(bundle.warnings),
                "skill": skill.value,
                "knowledge_gap_count": len(knowledge_gaps.gaps) if knowledge_gaps else 0,
                "graph_path_count": len(graph_paths),
                "ops_signal_count": len(ops_finding.signals) if ops_finding else 0,
                "context_pack": pack.audit_refs(),
                "context_prompt": prompt.audit_refs,
                "model_adapter": adapter_result.audit_refs(),
            },
        )
        analysis = self._analyzer.analyze(
            run.question,
            bundle,
            skill=skill,
            knowledge_gaps=knowledge_gaps,
            graph_paths=graph_paths,
            ops_finding=ops_finding,
        )
        await self._lifecycle.emit_async(
            LifecycleEventType.ANALYSIS_COMPLETED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=resolved.project,
            payload={
                "skill": analysis.skill.value,
                "candidate_count": len(analysis.candidates),
            },
        )

        await on_transition(
            RunStatus.VERIFYING,
            {"candidate_count": len(analysis.candidates), "skill": analysis.skill.value},
        )
        verification = self._verifier.verify(analysis, bundle, resolved.project)
        await self._lifecycle.emit_async(
            LifecycleEventType.VERIFICATION_COMPLETED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=resolved.project,
            payload={
                "claim_count": len(verification.claims),
                "issue_count": len(verification.issues),
            },
        )
        answer = self._composer.compose(resolved.project, analysis, verification, bundle)
        answer, enhancement_audit = maybe_enhance_answer(answer, adapter_result)
        await self._lifecycle.emit_async(
            LifecycleEventType.ANSWER_COMPOSED,
            run_id=run.id,
            trace_id=run.trace_id,
            project=resolved.project,
            payload={
                "skill": answer.skill,
                "claim_count": len(answer.claims),
                "evidence_count": len(answer.evidence),
                "expression_enhancement": enhancement_audit,
            },
        )
        answer = maybe_attach_engineering_proposal(
            answer,
            question=run.question,
            skill=skill,
            evidence=bundle.evidence,
            engineering_skill=self._engineering_skill,
        )
        eng = engineering_action_from_answer(answer)
        if eng is not None:
            await self._lifecycle.emit_async(
                LifecycleEventType.ENGINEERING_PROPOSED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=resolved.project,
                payload={
                    "action_id": str(eng.id),
                    "can_apply": bool(eng.arguments.get("can_apply", False)),
                    "requires_approval": eng.requires_approval,
                },
            )
            await self._lifecycle.emit_async(
                LifecycleEventType.ENGINEERING_VALIDATED,
                run_id=run.id,
                trace_id=run.trace_id,
                project=resolved.project,
                payload={
                    "action_id": str(eng.id),
                    "test_passed": bool(eng.arguments.get("test_passed", False)),
                    "allow_apply": False,
                },
            )
        task_state = merge_scratchpads(
            pack.task_state,
            scratchpad_from_ops_finding(ops_finding),
            scratchpad_from_answer(answer),
        )
        if task_state is not None:
            self._last_context_pack = pack.model_copy(update={"task_state": task_state})
        return answer


def _bundle_from_authorized(
    evidence: tuple[Evidence, ...],
    *,
    project: ProjectRef,
    question: str,
) -> EvidenceBundle:
    hits = tuple(
        RetrievalHit(
            evidence=item,
            score=1.0,
            channels=("authorized",),
            channel_ranks={"authorized": index + 1},
        )
        for index, item in enumerate(evidence)
    )
    return EvidenceBundle(
        query=ContextQuery(text=question, project=project, limit=max(len(hits), 1)),
        hits=hits,
        retrieval_trace={
            "authorized_candidate_count": len(hits),
            "channel_counts": {"authorized": len(hits)},
        },
        warnings=() if hits else ("no authorized project evidence matched the query",),
    )


def _collect_graph_paths(
    read_gateway: ReadContextGateway,
    *,
    project: ProjectRef,
    access: AccessContext,
    skill: ProjectSkill,
    question: str,
    bundle: EvidenceBundle,
) -> tuple[tuple[GraphEvidence, ...], EvidenceBundle]:
    queries = graph_queries_for_question(skill, question)
    if not queries:
        return (), bundle
    paths: list[GraphEvidence] = []
    seen_summaries: set[str] = set()
    for query in queries:
        for path in read_gateway.query_graph(project, access, query):
            if path.summary in seen_summaries:
                continue
            seen_summaries.add(path.summary)
            paths.append(path)
            if len(paths) >= MAX_GRAPH_PATHS_PER_RUN:
                break
        if len(paths) >= MAX_GRAPH_PATHS_PER_RUN:
            break
    if not paths:
        return (), bundle
    return tuple(paths), _enrich_bundle_with_graph_evidence(
        read_gateway, project, access, bundle, paths
    )


def _enrich_bundle_with_graph_evidence(
    read_gateway: ReadContextGateway,
    project: ProjectRef,
    access: AccessContext,
    bundle: EvidenceBundle,
    paths: list[GraphEvidence] | tuple[GraphEvidence, ...],
) -> EvidenceBundle:
    known = {item.id for item in bundle.evidence}
    needed = {
        evidence_id
        for path in paths
        for evidence_id in path.evidence_ids
        if evidence_id not in known
    }
    if not needed:
        return bundle
    authorized = read_gateway.authorized_evidence(project, access, limit=50)
    extras = [item for item in authorized if item.id in needed]
    if not extras:
        return bundle
    extra_hits = tuple(
        RetrievalHit(
            evidence=item,
            score=0.5,
            channels=("graph",),
            channel_ranks={"graph": index + 1},
        )
        for index, item in enumerate(extras)
    )
    hits = bundle.hits + extra_hits
    trace = dict(bundle.retrieval_trace)
    channel_counts = dict(trace.get("channel_counts") or {})
    channel_counts["graph"] = len(extra_hits)
    trace["channel_counts"] = channel_counts
    trace["graph_evidence_count"] = len(extra_hits)
    return EvidenceBundle(
        query=bundle.query,
        hits=hits,
        retrieval_trace=trace,
        warnings=bundle.warnings,
    )
