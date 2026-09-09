"""Multi-turn harness replay: execute, follow up, then run design probes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from project_lens.application.conversation_service import ConversationService
from project_lens.application.run_service import RunService
from project_lens.context.engine import ContextEngine
from project_lens.context.memory_store import MemoryStore
from project_lens.context.store import EvidenceIndex
from project_lens.domain.models import ProjectRef, RunStatus
from project_lens.evaluation.answer_metrics import answer_metrics
from project_lens.evaluation.harness_probes import HarnessProbeReport, run_harness_probes
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.workflow.followup import FollowupRewriter


@dataclass(frozen=True)
class ReplayStep:
    question: str
    user_id: str = "replay-user"


@dataclass(frozen=True)
class HarnessReplayReport:
    project: ProjectRef
    run_ids: tuple[str, ...]
    steps: tuple[dict[str, Any], ...]
    probes: HarnessProbeReport
    lifecycle_event_count: int
    observability: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.probes.passed and all(
            step.get("status") == RunStatus.COMPLETED.value for step in self.steps
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "project": {
                "tenant_id": self.project.tenant_id,
                "project_id": self.project.project_id,
            },
            "run_ids": list(self.run_ids),
            "lifecycle_event_count": self.lifecycle_event_count,
            "steps": list(self.steps),
            "probes": self.probes.as_dict(),
            "observability": dict(self.observability),
        }


def build_harness_observability(
    *,
    run_service: RunService,
    lifecycle: LifecycleBus,
) -> dict[str, Any]:
    """Answer Phase D acceptance questions from the last ContextPack/Prompt trail."""

    workflow = getattr(run_service, "_workflow", None)
    context_pack = workflow.last_context_pack if workflow is not None else None
    context_prompt = workflow.last_context_prompt if workflow is not None else None
    adapter = (
        workflow.last_model_adapter_result if workflow is not None else None
    )

    model_saw: dict[str, Any] = {"layers": [], "section_titles": []}
    compressed: dict[str, Any] = {}
    non_compressible: list[str] = []
    provenance: dict[str, Any] = {}
    if context_prompt is not None:
        model_saw = {
            "layers": [item.layer for item in context_prompt.sections],
            "section_titles": [item.title for item in context_prompt.sections],
            "message_roles": [item.role for item in context_prompt.messages],
            "evidence_ids": list(context_prompt.audit_refs.get("evidence_ids") or []),
            "memory_ids": list(context_prompt.audit_refs.get("memory_ids") or []),
            "skill": context_prompt.audit_refs.get("skill"),
        }
        compressed = dict(context_prompt.audit_refs.get("compression_manifest") or {})
        non_compressible = list(
            context_prompt.audit_refs.get("non_compressible") or []
        )
    if context_pack is not None:
        provenance = context_pack.provenance.model_dump()
        if not non_compressible:
            non_compressible = [
                "L0_anchors",
                "pinned_ids",
                "tool_policy_hash",
                "allow_apply=False",
            ]

    prompt_events = [
        event
        for event in lifecycle.all()
        if event.type == LifecycleEventType.CONTEXT_PROMPT_RENDERED
    ]
    read_audit: dict[str, Any] = {}
    if workflow is not None and getattr(workflow, "read_gateway", None) is not None:
        read_audit = workflow.read_gateway.audit_summary()
    else:
        for event in lifecycle.all():
            if event.type == LifecycleEventType.CONTEXT_COLLECTED:
                raw = event.payload.get("read_gateway")
                if isinstance(raw, dict):
                    read_audit = dict(raw)
                    break
    return {
        "模型看到了什么": model_saw,
        "这些上下文从哪里来": {
            **provenance,
            "read_gateway": read_audit,
        },
        "哪些内容被压缩了": compressed,
        "哪些内容不能压缩": non_compressible,
        "如果回答错了，能不能 replay": {
            "prompt_rendered_events": len(prompt_events),
            "used_prompt": bool(adapter.used_prompt) if adapter is not None else False,
            "replay_api": "replay_harness_conversation",
            "read_tool_names": list(read_audit.get("read_tool_names") or []),
        },
    }


async def replay_harness_conversation(
    *,
    run_service: RunService,
    conversation: ConversationService,
    project: ProjectRef,
    steps: Sequence[ReplayStep | dict[str, str]],
    chat_id: str = "replay-chat",
    memory_store: MemoryStore | None = None,
    context_engine: ContextEngine | None = None,
    evidence_index: EvidenceIndex | None = None,
    lifecycle: LifecycleBus | None = None,
    pending_proposal_ids: tuple[UUID, ...] = (),
) -> HarnessReplayReport:
    """Replay a multi-turn question sequence with session + follow-up rewrite."""

    bus = lifecycle or getattr(run_service, "lifecycle", None) or LifecycleBus()
    rewriter = FollowupRewriter()
    normalized = [
        step
        if isinstance(step, ReplayStep)
        else ReplayStep(question=step["question"], user_id=step.get("user_id", "replay-user"))
        for step in steps
    ]
    session = conversation.get_or_create(
        tenant_id=project.tenant_id,
        chat_id=chat_id,
        user_id=normalized[0].user_id if normalized else "replay-user",
        project=project,
    )
    step_results: list[dict[str, Any]] = []
    run_ids: list[str] = []
    last_answer = None

    for index, step in enumerate(normalized):
        question, followup = conversation.prepare_question(session, step.question)
        if followup is None:
            question = step.question
        run = run_service.create(
            project=project,
            user_id=step.user_id,
            channel_id=chat_id,
            question=question,
        )
        completed = await run_service.execute(
            run.id,
            session=session,
            memories=() if memory_store is None else memory_store.list_memories(project),
        )
        if completed is None:
            step_results.append(
                {
                    "index": index,
                    "raw_question": step.question,
                    "question": question,
                    "followup_rewrite": followup,
                    "status": "missing",
                    "run_id": str(run.id),
                }
            )
            run_ids.append(str(run.id))
            continue
        run_ids.append(str(completed.id))
        answer = completed.answer
        last_answer = answer
        session = conversation.record_turn(
            session,
            user_id=step.user_id,
            text=step.question,
            rewritten_question=followup,
            run_id=completed.id,
            answer=answer if completed.status == RunStatus.COMPLETED else None,
            task_state=None,
        )
        result: dict[str, Any] = {
            "index": index,
            "raw_question": step.question,
            "question": question,
            "followup_rewrite": followup,
            "status": completed.status.value,
            "run_id": str(completed.id),
            "trace_id": str(completed.trace_id),
            "active_skill": session.summary.active_skill,
        }
        if answer is not None:
            result["metrics"] = answer_metrics(answer)
            result["skill"] = answer.skill
        else:
            result["error"] = completed.error
        # Keep a lightweight recall check on later turns.
        if index > 0:
            result["recall_rewrite"] = rewriter.rewrite("影响哪里？", session)
        step_results.append(result)

    probes = run_harness_probes(
        answer=last_answer,
        session=session,
        memory_store=memory_store,
        context_engine=context_engine,
        evidence_index=evidence_index,
        lifecycle=bus,
        pending_proposal_ids=pending_proposal_ids,
    )
    return HarnessReplayReport(
        project=project,
        run_ids=tuple(run_ids),
        steps=tuple(step_results),
        probes=probes,
        lifecycle_event_count=len(bus.all()),
        observability=build_harness_observability(
            run_service=run_service,
            lifecycle=bus,
        ),
    )


def summarize_run_lifecycle(
    lifecycle: LifecycleBus,
    run_id: UUID,
) -> dict[str, Any]:
    events = lifecycle.for_run(run_id)
    return {
        "run_id": str(run_id),
        "event_count": len(events),
        "types": [event.type.value for event in events],
    }
