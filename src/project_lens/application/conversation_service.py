"""Conversation session lifecycle for multi-turn Feishu collaboration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from project_lens.context.conversation_store import ConversationStore, InMemoryConversationStore
from project_lens.domain.conversation import (
    DEFAULT_RECENT_TURN_LIMIT,
    DEFAULT_SESSION_TTL,
    ConversationSession,
    ConversationTurn,
    empty_summary,
)
from project_lens.domain.models import ProjectAnswer, ProjectRef
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType
from project_lens.workflow.context_compression import (
    compress_overflow_into_summary,
    compress_scratchpad,
    extract_pinned_from_scratchpad,
    merge_answer_into_summary,
    merge_pinned_ids,
    pin_prior_traceback,
)
from project_lens.workflow.context_pack import TaskScratchpad, task_scratchpad_from_session
from project_lens.workflow.followup import FollowupRewriter
from project_lens.workflow.task_scratchpad import (
    merge_scratchpads,
    scratchpad_from_answer,
    scratchpad_to_session_dict,
)


class ConversationService:
    """Owns L1 recent turns and L2 summary updates. Never writes ProjectMemory."""

    def __init__(
        self,
        store: ConversationStore | None = None,
        *,
        rewriter: FollowupRewriter | None = None,
        recent_turn_limit: int = DEFAULT_RECENT_TURN_LIMIT,
        session_ttl: timedelta = DEFAULT_SESSION_TTL,
        lifecycle: LifecycleBus | None = None,
    ) -> None:
        self._store = store or InMemoryConversationStore()
        self._rewriter = rewriter or FollowupRewriter()
        self._recent_turn_limit = max(2, recent_turn_limit)
        self._session_ttl = session_ttl
        self._lifecycle = lifecycle or LifecycleBus()

    @property
    def store(self) -> ConversationStore:
        return self._store

    def get_or_create(
        self,
        *,
        tenant_id: str,
        chat_id: str,
        user_id: str,
        project: ProjectRef,
    ) -> ConversationSession:
        existing = self._store.get_by_binding(
            tenant_id=tenant_id,
            chat_id=chat_id,
            user_id=user_id,
            project=project,
        )
        if existing is not None:
            self._emit_session_event(
                LifecycleEventType.SESSION_LOADED,
                existing,
                extra={"recovered": True},
            )
            return existing
        session = ConversationSession(
            tenant_id=tenant_id,
            chat_id=chat_id,
            user_id=user_id,
            project=project,
            summary=empty_summary(project),
            expires_at=datetime.now(timezone.utc) + self._session_ttl,
        )
        saved = self._store.upsert(session)
        self._emit_session_event(LifecycleEventType.SESSION_CREATED, saved)
        self._emit_session_event(LifecycleEventType.SESSION_SAVED, saved)
        return saved

    def prepare_question(
        self,
        session: ConversationSession,
        text: str,
    ) -> tuple[str, str | None]:
        """Return (question_for_run, followup_rewrite_or_none)."""

        from project_lens.workflow.skills import skill_routing_enabled

        if not skill_routing_enabled():
            return (text, None)
        followup = self._rewriter.rewrite(text, session)
        return (followup or text, followup)

    def attach_runtime_access(
        self,
        session: ConversationSession,
        runtime_access: dict[str, object],
    ) -> ConversationSession:
        """Persist EffectiveAccessScope audit bounds on a frozen session."""

        merged = {**session.task_scratchpad, "runtime_access": runtime_access}
        updated = session.model_copy(update={"task_scratchpad": merged})
        saved = self._store.upsert(updated)
        self._emit_session_event(LifecycleEventType.SESSION_SAVED, saved)
        return saved

    def record_turn(
        self,
        session: ConversationSession,
        *,
        user_id: str,
        text: str,
        rewritten_question: str | None,
        run_id: UUID | None = None,
        answer: ProjectAnswer | None = None,
        task_state: TaskScratchpad | None = None,
    ) -> ConversationSession:
        turn = ConversationTurn(
            run_id=run_id,
            user_id=user_id,
            text=text,
            rewritten_question=rewritten_question,
        )
        recent = session.recent_turns + (turn,)
        overflow = recent[: -self._recent_turn_limit]
        kept = recent[-self._recent_turn_limit :]
        # Pin traceback into L2 immediately so restart/compress cannot drop it.
        summary = pin_prior_traceback(
            session.summary,
            text,
            rewritten_question,
        )
        if overflow:
            self._lifecycle.emit(
                LifecycleEventType.COMPRESSION_TRIGGERED,
                run_id=run_id,
                project=session.project,
                payload={
                    "session_id": str(session.session_id),
                    "overflow_count": len(overflow),
                    "recent_turn_limit": self._recent_turn_limit,
                    "cycle_before": summary.compression_cycle,
                },
            )
            summary = compress_overflow_into_summary(summary, overflow)
            self._lifecycle.emit(
                LifecycleEventType.COMPRESSION_COMPLETED,
                run_id=run_id,
                project=session.project,
                payload={
                    "session_id": str(session.session_id),
                    "compression_cycle": summary.compression_cycle,
                    "pinned_evidence_count": len(summary.pinned_ids.evidence_ids),
                    "pinned_file_count": len(summary.pinned_ids.file_paths),
                    "compressed_turns": summary.active_topic.get("compressed_turns"),
                },
            )
        if answer is not None:
            summary = merge_answer_into_summary(summary, answer)
        merged_task = compress_scratchpad(
            merge_scratchpads(
                task_scratchpad_from_session(session),
                scratchpad_from_answer(answer),
                task_state,
            )
        )
        if merged_task is not None:
            summary = summary.model_copy(
                update={
                    "pinned_ids": merge_pinned_ids(
                        summary.pinned_ids,
                        extract_pinned_from_scratchpad(merged_task),
                    )
                }
            )
        update: dict[str, object] = {
            "recent_turns": kept,
            "summary": summary,
            "last_run_id": run_id if run_id is not None else session.last_run_id,
            "expires_at": datetime.now(timezone.utc) + self._session_ttl,
        }
        if merged_task is not None:
            scratchpad = scratchpad_to_session_dict(merged_task)
            current_access = session.task_scratchpad.get("runtime_access")
            if current_access is not None:
                scratchpad["runtime_access"] = current_access
            update["task_scratchpad"] = scratchpad
        updated = session.model_copy(update=update)
        saved = self._store.upsert(updated)
        self._emit_session_event(
            LifecycleEventType.SESSION_SAVED,
            saved,
            run_id=run_id,
        )
        return saved

    def attach_hermes_tool_loop(
        self,
        session: ConversationSession,
        entry: dict[str, object],
        *,
        keep_last: int = 5,
    ) -> ConversationSession:
        """Persist a compact Hermes tool-loop audit pointer on the session scratchpad.

        Does not create an AgentRun. ``loop_id`` is a synthetic key for agent_events.
        """

        recent = [
            item
            for item in (session.task_scratchpad.get("hermes_tool_loops") or [])
            if isinstance(item, dict)
        ]
        recent.append(dict(entry))
        recent = recent[-max(1, keep_last) :]
        merged = {
            **session.task_scratchpad,
            "hermes_tool_loop": dict(entry),
            "hermes_tool_loops": recent,
        }
        updated = session.model_copy(update={"task_scratchpad": merged})
        saved = self._store.upsert(updated)
        self._emit_session_event(LifecycleEventType.SESSION_SAVED, saved)
        return saved

    def _emit_session_event(
        self,
        event_type: LifecycleEventType,
        session: ConversationSession,
        *,
        run_id: UUID | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "session_id": str(session.session_id),
            "tenant_id": session.tenant_id,
            "chat_id": session.chat_id,
            "user_id": session.user_id,
            "project_id": session.project.project_id,
            "compression_cycle": session.summary.compression_cycle,
            "recent_turn_count": len(session.recent_turns),
            "has_prior_traceback": bool(
                (session.summary.active_topic or {}).get("prior_traceback")
            ),
            "active_skill": session.summary.active_skill,
        }
        if extra:
            payload.update(extra)
        self._lifecycle.emit(
            event_type,
            run_id=run_id or session.last_run_id,
            project=session.project,
            payload=payload,
        )
