"""The sole production orchestration path for Hermes project questions."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from project_lens.application.run_service import RunService
from project_lens.domain.identity import ActorContext
from project_lens.domain.models import AgentRun, ProjectAnswer, ProjectRef
from project_lens.integrations.feishu.hermes_context import (
    HermesProjectContext,
    build_hermes_project_context,
)
from project_lens.integrations.feishu.hermes_tool_loop import (
    FeishuHermesToolLoopBridge,
    FeishuHermesToolLoopResult,
)
from project_lens.project_space.policies import (
    EffectiveAccessScope,
    effective_scope_to_audit_dict,
)
from project_lens.workflow.context_snapshot import (
    ContextSnapshotService,
    MemoryCandidate,
)


@dataclass(frozen=True)
class HermesExecutionResult:
    run: AgentRun
    answer: ProjectAnswer | None
    loop_id: UUID
    tool_names: tuple[str, ...]
    envelope: dict[str, object]
    context: HermesProjectContext

    @property
    def ok(self) -> bool:
        return self.answer is not None and self.run.status.value == "completed"


@dataclass(frozen=True)
class PendingHermesExecution:
    """A bound Hermes run that is safe to hand to background execution."""

    run: AgentRun
    project: ProjectRef
    actor: ActorContext
    scope: EffectiveAccessScope
    question: str
    entry_mode: str
    loop_id: UUID
    context: HermesProjectContext
    context_snapshot_id: UUID | None = None


class HermesRuntimeService:
    """Create the real AgentRun before delegating decision-making to Hermes."""

    def __init__(
        self,
        *,
        run_service: RunService,
        bridge: FeishuHermesToolLoopBridge,
        context_snapshots: ContextSnapshotService | None = None,
    ) -> None:
        self._run_service = run_service
        self._bridge = bridge
        self._context_snapshots = context_snapshots

    @property
    def run_service(self) -> RunService:
        return self._run_service

    async def execute(
        self,
        *,
        project: ProjectRef,
        actor: ActorContext,
        scope: EffectiveAccessScope,
        question: str,
        entry_mode: str,
        context_snapshot_id: UUID | None = None,
        context: HermesProjectContext | None = None,
        memory_candidates: tuple[MemoryCandidate, ...] = (),
    ) -> HermesExecutionResult:
        pending = self.prepare(
            project=project,
            actor=actor,
            scope=scope,
            question=question,
            entry_mode=entry_mode,
            context_snapshot_id=context_snapshot_id,
            context=context,
            memory_candidates=memory_candidates,
        )
        return await self.execute_prepared(pending)

    def prepare(
        self,
        *,
        project: ProjectRef,
        actor: ActorContext,
        scope: EffectiveAccessScope,
        question: str,
        entry_mode: str,
        context_snapshot_id: UUID | None = None,
        context: HermesProjectContext | None = None,
        memory_candidates: tuple[MemoryCandidate, ...] = (),
    ) -> PendingHermesExecution:
        """Validate and persist the run before the Hermes tool loop starts."""

        self._assert_binding(project=project, actor=actor, scope=scope)
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be empty")

        runtime_access = effective_scope_to_audit_dict(scope)
        hermes_context = context or build_hermes_project_context(
            session=None,
            runtime_access=runtime_access,
        )
        if self._context_snapshots is not None and memory_candidates:
            frozen = self._context_snapshots.freeze_and_start(
                project=project,
                scope=scope,
                candidates=memory_candidates,
            )
            snapshot = frozen.snapshot
            context_snapshot_id = snapshot.snapshot_id
            context = build_hermes_project_context(
                session=None,
                memories=frozen.memories,
                runtime_access=runtime_access,
            )
            hermes_context = context
            hermes_context.audit_refs.update({
                "context_snapshot_id": str(snapshot.snapshot_id),
                "renderer_version": snapshot.renderer_version,
                "snapshot_memory_ids": [str(item) for item in snapshot.included_memory_ids],
            })
        context_hash = sha256(hermes_context.text.encode("utf-8")).hexdigest()
        loop_id = uuid4()
        run = self._run_service.create(
            project=project,
            user_id=actor.actor_id,
            channel_id=actor.chat_id,
            question=normalized_question,
            runtime_access=runtime_access,
            entry_mode=entry_mode,
            runtime="hermes",
            hermes_loop_id=loop_id,
            context_snapshot_id=context_snapshot_id,
            context_hash=context_hash,
        )
        return PendingHermesExecution(
            run=run,
            project=project,
            actor=actor,
            scope=scope,
            question=normalized_question,
            entry_mode=entry_mode,
            loop_id=loop_id,
            context=hermes_context,
            context_snapshot_id=context_snapshot_id,
        )

    async def execute_prepared(
        self,
        pending: PendingHermesExecution,
    ) -> HermesExecutionResult:
        """Run a previously-created Hermes execution without making a second run."""

        run = pending.run
        loop_id = pending.loop_id
        result: FeishuHermesToolLoopResult
        try:
            result = await self._bridge.answer(
                project=pending.project,
                question=pending.question,
                user_id=pending.actor.actor_id,
                chat_id=pending.actor.chat_id,
                context=pending.context,
                run_id=run.id,
                loop_id=loop_id,
                trace_id=run.trace_id,
            )
            error = None
            answer = result.verified_answer if result.ok else None
            if answer is None:
                error = str(
                    result.envelope.get("audit_ref", {}).get("error")
                    if isinstance(result.envelope.get("audit_ref"), dict)
                    else ""
                ) or "Hermes tool loop failed verification"
            completed = await self._run_service.complete_external(
                run.id,
                answer=answer,
                error=error,
                agent_mode="hermes",
                hermes_loop_id=loop_id,
                metadata={
                    "entry_mode": pending.entry_mode,
                    "hermes_loop_id": str(loop_id),
                    "tool_names": list(result.tool_names),
                    "context_hash": run.context_hash,
                    "context_snapshot_id": (
                        str(pending.context_snapshot_id)
                        if pending.context_snapshot_id is not None
                        else None
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            completed = await self._run_service.complete_external(
                run.id,
                answer=None,
                error=f"Hermes runtime failed: {exc}",
                agent_mode="hermes",
                hermes_loop_id=loop_id,
                metadata={
                    "entry_mode": pending.entry_mode,
                    "hermes_loop_id": str(loop_id),
                },
            )
            result = FeishuHermesToolLoopResult(
                ok=False,
                envelope={"ok": False, "audit_ref": {"error": str(exc)}},
                loop_id=loop_id,
                trace_id=run.trace_id,
            )
        if completed is None:
            raise RuntimeError(f"Hermes run {run.id} disappeared before completion")
        return HermesExecutionResult(
            run=completed,
            answer=completed.answer,
            loop_id=loop_id,
            tool_names=result.tool_names,
            envelope=result.envelope,
            context=pending.context,
        )

    @staticmethod
    def _assert_binding(
        *,
        project: ProjectRef,
        actor: ActorContext,
        scope: EffectiveAccessScope,
    ) -> None:
        if not actor.authenticated:
            raise PermissionError("Hermes runtime requires an authenticated actor")
        if actor.tenant_key != project.tenant_id:
            raise PermissionError("actor tenant does not match project")
        if scope.project != project:
            raise PermissionError("effective access scope does not match project")
        if scope.actor_id != actor.actor_id or scope.chat_id != actor.chat_id:
            raise PermissionError("effective access scope is not bound to this actor/chat")
        if scope.chat_type != actor.chat_type:
            raise PermissionError("effective access scope chat type does not match actor")
        if scope.identity_source != actor.source:
            raise PermissionError("effective access scope identity source does not match actor")
