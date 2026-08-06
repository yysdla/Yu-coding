"""L3 TaskScratchpad builders: deterministic compression from answer/events/ops/sync.

Raw AgentEvents remain append-only; this module only derives structured task state.
"""

from __future__ import annotations

from typing import Any

from project_lens.domain.feishu_doc_sync import FeishuDocSyncStatus
from project_lens.domain.models import ActionProposal, ProjectAnswer
from project_lens.domain.ops import OpsFinding
from project_lens.runtime.events import AgentEvent, AgentEventType
from project_lens.workflow.context_pack import TaskScratchpad
from project_lens.workflow.engineering_bridge import engineering_action_from_answer


def merge_scratchpads(
    *parts: TaskScratchpad | None,
) -> TaskScratchpad | None:
    """Later non-empty fields win; list fields append and dedupe."""

    active = [part for part in parts if part is not None]
    if not active:
        return None
    merged = active[0]
    for part in active[1:]:
        merged = TaskScratchpad(
            phase=part.phase or merged.phase,
            plan=part.plan or merged.plan,
            files_read=_dedupe(merged.files_read + part.files_read),
            files_changed=_dedupe(merged.files_changed + part.files_changed),
            patch_plan=part.patch_plan or merged.patch_plan,
            diff_summary=part.diff_summary or merged.diff_summary,
            test_commands=_dedupe(merged.test_commands + part.test_commands),
            test_results=_dedupe(merged.test_results + part.test_results),
            approval_status=part.approval_status or merged.approval_status,
            allow_apply=False,
            sync_state={**merged.sync_state, **part.sync_state},
            failed_attempts=_dedupe(merged.failed_attempts + part.failed_attempts),
            rejected_plans=_dedupe(merged.rejected_plans + part.rejected_plans),
            tool_audit_events=_dedupe(
                merged.tool_audit_events + part.tool_audit_events
            ),
            notes={**merged.notes, **part.notes},
        )
    if merged.allow_apply:
        return merged.model_copy(update={"allow_apply": False})
    return merged


def scratchpad_from_engineering_action(action: ActionProposal) -> TaskScratchpad:
    args = action.arguments
    paths = tuple(str(item) for item in args.get("affected_paths") or ())
    test_passed = bool(args.get("test_passed"))
    diff_summary = str(args.get("diff_summary") or "")[:800] or None
    explanation = str(args.get("explanation") or action.description or "")[:500] or None
    patch_plan_text = str(args.get("patch_plan_text") or "").strip() or explanation
    commands_raw = args.get("test_commands") or ()
    if isinstance(commands_raw, (list, tuple)):
        test_commands = tuple(str(item) for item in commands_raw if str(item).strip())
    else:
        test_commands = (str(commands_raw),) if str(commands_raw).strip() else ()
    if not test_commands:
        test_commands = ("worktree_validate",)
    test_results = (
        (f"test_passed={test_passed}",)
        + ((f"test_output={str(args.get('test_output_summary') or '')[:200]}",)
           if args.get("test_output_summary")
           else ())
    )
    failed_raw = args.get("failed_attempts") or ()
    if isinstance(failed_raw, (list, tuple)):
        failed = tuple(str(item) for item in failed_raw if str(item).strip())
    else:
        failed = (str(failed_raw),) if str(failed_raw).strip() else ()
    if not test_passed and not failed:
        failed = ("engineering_validation_failed",)
    return TaskScratchpad(
        phase="Validate",
        plan=explanation,
        files_read=paths,
        files_changed=paths,
        patch_plan=patch_plan_text,
        diff_summary=diff_summary,
        test_commands=test_commands,
        test_results=test_results,
        approval_status="pending" if action.requires_approval else "not_required",
        allow_apply=False,
        failed_attempts=failed,
        notes={
            "engineering_action_id": str(action.id),
            "can_apply": "False",
            "allow_apply": "False",
            "requires_approval": str(bool(action.requires_approval)),
            "validate_mode": str(args.get("validate_mode") or "isolated_worktree"),
        },
    )


def scratchpad_from_answer(answer: ProjectAnswer | None) -> TaskScratchpad | None:
    if answer is None:
        return None
    action = engineering_action_from_answer(answer)
    base = TaskScratchpad(
        phase="Answered",
        allow_apply=False,
        notes={
            "skill": answer.skill,
            "status": answer.status,
            "answer_claim_count": str(len(answer.claims)),
            "answer_evidence_count": str(len(answer.evidence)),
        },
    )
    if action is None:
        return base
    return merge_scratchpads(base, scratchpad_from_engineering_action(action))


def scratchpad_from_ops_finding(finding: OpsFinding | None) -> TaskScratchpad | None:
    if finding is None or not finding.signals:
        return None
    kinds = sorted({signal.kind.value for signal in finding.signals})
    return TaskScratchpad(
        phase="OpsQuery",
        allow_apply=False,
        notes={
            "ops_signal_count": str(len(finding.signals)),
            "ops_kinds": ",".join(kinds),
            "ops_summary": (finding.summary or "")[:300],
            "ops_window_start": finding.query.start.isoformat(),
            "ops_window_end": finding.query.end.isoformat(),
        },
    )


def scratchpad_from_sync_statuses(
    statuses: tuple[FeishuDocSyncStatus, ...],
) -> TaskScratchpad:
    sync_state: dict[str, str] = {
        "status_count": str(len(statuses)),
        "failed_count": str(
            sum(1 for item in statuses if item.status.value == "failed")
        ),
        "success_count": str(
            sum(1 for item in statuses if item.status.value == "success")
        ),
    }
    for item in statuses[:20]:
        revision = item.revision or "-"
        sync_state[f"doc:{item.doc_token}"] = f"{item.status.value}@{revision}"
        if item.access_scope:
            sync_state[f"scope:{item.doc_token}"] = item.access_scope
        if item.doc_url:
            sync_state[f"url:{item.doc_token}"] = item.doc_url[:300]
        if item.last_success_revision:
            sync_state[f"last_ok:{item.doc_token}"] = item.last_success_revision
        if item.error:
            sync_state[f"error:{item.doc_token}"] = item.error[:200]
        if item.last_synced_at is not None:
            sync_state[f"synced_at:{item.doc_token}"] = item.last_synced_at.isoformat()
    return TaskScratchpad(
        phase="DocSyncStatus",
        allow_apply=False,
        sync_state=sync_state,
        approval_status="not_required",
    )


def compress_events_to_scratchpad(
    events: tuple[AgentEvent, ...],
    *,
    prior: TaskScratchpad | None = None,
    answer: ProjectAnswer | None = None,
    ops_finding: OpsFinding | None = None,
) -> TaskScratchpad | None:
    """Compress append-only RunEvents (+ answer/ops) into L3 task state."""

    tool_events: list[str] = []
    notes: dict[str, str] = {}
    phase: str | None = None
    for event in events:
        if event.type in {
            AgentEventType.TOOL_STARTED,
            AgentEventType.TOOL_COMPLETED,
            AgentEventType.TOOL_DENIED,
        }:
            tool_name = str(event.payload.get("tool_name") or event.type.value)
            tool_events.append(f"{event.type.value}:{tool_name}")
        if event.type == AgentEventType.RUN_STATUS_CHANGED:
            status = str(event.payload.get("status") or "")
            if status:
                phase = status[:1].upper() + status[1:] if status else phase
            if "ops_signal_count" in event.payload:
                notes["ops_signal_count"] = str(event.payload["ops_signal_count"])
            if "evidence_count" in event.payload:
                notes["evidence_count"] = str(event.payload["evidence_count"])
            pack_refs = event.payload.get("context_pack")
            if isinstance(pack_refs, dict):
                if pack_refs.get("session_id"):
                    notes["session_id"] = str(pack_refs["session_id"])
                if pack_refs.get("skill"):
                    notes["skill"] = str(pack_refs["skill"])
        if event.type == AgentEventType.RUN_FAILED:
            notes["run_error"] = str(event.payload.get("error") or "failed")[:300]

    from_events = TaskScratchpad(
        phase=phase,
        allow_apply=False,
        tool_audit_events=tuple(tool_events[-40:]),
        notes=notes,
        failed_attempts=(
            (f"run_failed:{notes['run_error']}",) if "run_error" in notes else ()
        ),
    )
    return merge_scratchpads(
        prior,
        from_events if (tool_events or notes or phase) else None,
        scratchpad_from_ops_finding(ops_finding),
        scratchpad_from_answer(answer),
    )


def scratchpad_to_session_dict(scratchpad: TaskScratchpad | None) -> dict[str, Any]:
    if scratchpad is None:
        return {}
    payload = scratchpad.model_dump(mode="json")
    payload["allow_apply"] = False
    return payload


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))
