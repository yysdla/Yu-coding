"""Pure, deterministic evaluators for the six MVP project risk types."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from project_lens.domain.models import Evidence, EvidenceType, ProjectRef
from project_lens.domain.risk import (
    RiskFinding,
    RiskSeverity,
    RiskState,
    RiskType,
    ensure_utc,
    evidence_signature,
    stable_risk_id,
)


@dataclass(frozen=True)
class RiskRuleConfig:
    overdue_high_after: timedelta = timedelta(days=7)
    review_wait_threshold: timedelta = timedelta(hours=24)
    review_high_after: timedelta = timedelta(hours=72)


RiskRule = Callable[[Sequence[Evidence], datetime, RiskRuleConfig], tuple[RiskFinding, ...]]

_COMPLETED_STATUSES = {
    "done",
    "completed",
    "complete",
    "closed",
    "cancelled",
    "canceled",
    "resolved",
    "已完成",
    "完成",
    "已取消",
}
_BLOCKED_STATUSES = {"blocked", "waiting", "on_hold", "stalled", "阻塞", "等待中"}
_ACTIVE_CODE_STATUSES = {
    "in_progress",
    "doing",
    "developing",
    "ready",
    "ready_to_release",
    "done",
    "completed",
    "进行中",
    "待发布",
    "已完成",
}
_RELEASE_READY_STATUSES = {
    "ready",
    "ready_to_release",
    "release_ready",
    "done",
    "completed",
    "待发布",
    "已完成",
}
_FAILED_CI = {"failed", "failure", "error", "cancelled", "canceled", "失败"}
_BLOCKER_TERMS = (
    "明确阻塞",
    "当前阻塞",
    "被阻塞",
    "卡住",
    "无法继续",
    "blocked",
    "blocking",
    "cannot proceed",
    "can't proceed",
)
_RESOLVED_TERMS = (
    "已解决",
    "已经解决",
    "不再阻塞",
    "解除阻塞",
    "not blocked",
    "resolved",
    "unblocked",
)


def default_risk_rules() -> tuple[RiskRule, ...]:
    return (
        overdue_task_rule,
        blocked_dependency_rule,
        unsynced_requirement_rule,
        code_status_mismatch_rule,
        pr_review_or_ci_rule,
        chat_blocker_rule,
    )


def overdue_task_rule(
    evidence: Sequence[Evidence],
    now: datetime,
    config: RiskRuleConfig,
) -> tuple[RiskFinding, ...]:
    current = ensure_utc(now)
    findings: list[RiskFinding] = []
    for item in _tasks(evidence):
        status = _status(item)
        due_at = _datetime_value(item.metadata.get("due_at") or item.metadata.get("deadline"))
        if due_at is None or status in _COMPLETED_STATUSES or ensure_utc(due_at) >= current:
            continue
        overdue_by = current - ensure_utc(due_at)
        severity = (
            RiskSeverity.HIGH
            if overdue_by >= config.overdue_high_after
            else RiskSeverity.MEDIUM
        )
        task_ref = _task_ref(item)
        findings.append(
            _finding(
                project=item.project,
                risk_type=RiskType.OVERDUE_TASK,
                severity=severity,
                title=f"任务 {task_ref} 已逾期",
                summary=f"截止时间 {ensure_utc(due_at).isoformat()} 已过，当前状态为 {status or 'unknown'}。",
                primary_ref=task_ref,
                owner_ids=_owners(item),
                affected_refs=(task_ref,),
                support=(item,),
                now=current,
                due_at=ensure_utc(due_at),
            )
        )
    return tuple(findings)


def blocked_dependency_rule(
    evidence: Sequence[Evidence],
    now: datetime,
    config: RiskRuleConfig,
) -> tuple[RiskFinding, ...]:
    del config
    tasks = tuple(_tasks(evidence))
    by_ref = {_task_ref(item): item for item in tasks}
    findings: list[RiskFinding] = []
    for item in tasks:
        if _status(item) in _COMPLETED_STATUSES:
            continue
        dependency_ids = _string_tuple(
            item.metadata.get("dependency_ids")
            or item.metadata.get("blocked_by")
            or item.metadata.get("dependencies")
        )
        explicit_status = _normalized(
            item.metadata.get("dependency_status") or item.metadata.get("blocked_by_status")
        )
        blocked_dependencies = tuple(
            ref
            for ref in dependency_ids
            if ref in by_ref and _status(by_ref[ref]) in _BLOCKED_STATUSES
        )
        if explicit_status not in _BLOCKED_STATUSES and not blocked_dependencies:
            continue
        support = [item]
        support.extend(by_ref[ref] for ref in blocked_dependencies)
        task_ref = _task_ref(item)
        affected = tuple(dict.fromkeys((task_ref, *dependency_ids)))
        findings.append(
            _finding(
                project=item.project,
                risk_type=RiskType.BLOCKED_DEPENDENCY,
                severity=RiskSeverity.HIGH,
                title=f"任务 {task_ref} 受阻塞依赖影响",
                summary="前置任务或外部依赖处于 blocked/waiting，且当前任务尚未完成。",
                primary_ref=task_ref,
                owner_ids=_owners(item),
                affected_refs=affected,
                support=tuple(support),
                now=now,
            )
        )
    return tuple(findings)


def unsynced_requirement_rule(
    evidence: Sequence[Evidence],
    now: datetime,
    config: RiskRuleConfig,
) -> tuple[RiskFinding, ...]:
    del config
    tasks = {_task_ref(item): item for item in _tasks(evidence)}
    findings: list[RiskFinding] = []
    for requirement in evidence:
        if not _is_requirement(requirement):
            continue
        requirement_ref = str(
            requirement.metadata.get("requirement_id")
            or requirement.metadata.get("doc_token")
            or requirement.source.source_id
        )
        linked_refs = _string_tuple(
            requirement.metadata.get("linked_task_ids")
            or requirement.metadata.get("task_ids")
            or requirement.metadata.get("related_task_ids")
        )
        linked_tasks = tuple(tasks[ref] for ref in linked_refs if ref in tasks)
        acceptance = requirement.metadata.get("acceptance_criteria")
        acceptance_missing = requirement.metadata.get("acceptance_criteria_present") is False or (
            acceptance is not None and not _has_meaningful_value(acceptance)
        )
        if requirement.metadata.get("acceptance_criteria_required") is True and acceptance is None:
            acceptance_missing = True
        newer_than_tasks = tuple(
            task
            for task in linked_tasks
            if ensure_utc(requirement.observed_at) > ensure_utc(task.observed_at)
        )
        if not acceptance_missing and not newer_than_tasks:
            continue
        support = (requirement, *newer_than_tasks)
        owners = _owners(requirement) or _merge_owners(linked_tasks)
        reasons: list[str] = []
        if acceptance_missing:
            reasons.append("验收标准缺失")
        if newer_than_tasks:
            reasons.append("需求更新时间晚于关联研发/测试任务")
        severity = (
            RiskSeverity.HIGH
            if any(_status(task) in _RELEASE_READY_STATUSES for task in newer_than_tasks)
            else RiskSeverity.MEDIUM
        )
        findings.append(
            _finding(
                project=requirement.project,
                risk_type=RiskType.UNSYNCED_REQUIREMENT,
                severity=severity,
                title=f"需求 {requirement_ref} 未同步",
                summary="；".join(reasons) + "。",
                primary_ref=requirement_ref,
                owner_ids=owners,
                affected_refs=tuple(dict.fromkeys((requirement_ref, *linked_refs))),
                support=support,
                now=now,
            )
        )
    return tuple(findings)


def code_status_mismatch_rule(
    evidence: Sequence[Evidence],
    now: datetime,
    config: RiskRuleConfig,
) -> tuple[RiskFinding, ...]:
    del config
    available_refs = _code_refs(evidence)
    findings: list[RiskFinding] = []
    for item in _tasks(evidence):
        status = _status(item)
        if status not in _ACTIVE_CODE_STATUSES:
            continue
        expected_refs = tuple(
            dict.fromkeys(
                (
                    *_string_tuple(item.metadata.get("related_commit_sha")),
                    *_string_tuple(item.metadata.get("related_pr_id")),
                    *_string_tuple(item.metadata.get("branch")),
                    *_string_tuple(item.metadata.get("code_refs")),
                )
            )
        )
        requires_code = item.metadata.get("requires_code") is True
        if not expected_refs and not requires_code:
            continue
        missing_refs = tuple(ref for ref in expected_refs if ref not in available_refs)
        if expected_refs and not missing_refs:
            continue
        task_ref = _task_ref(item)
        reason = (
            f"关联代码证据不存在或不匹配：{', '.join(missing_refs)}"
            if missing_refs
            else "任务要求代码交付，但未关联分支、PR、Commit 或 CI 证据"
        )
        findings.append(
            _finding(
                project=item.project,
                risk_type=RiskType.CODE_STATUS_MISMATCH,
                severity=(
                    RiskSeverity.HIGH
                    if status in _COMPLETED_STATUSES or status in _RELEASE_READY_STATUSES
                    else RiskSeverity.MEDIUM
                ),
                title=f"任务 {task_ref} 与代码状态不一致",
                summary=f"{reason}，任务状态为 {status}。",
                primary_ref=task_ref,
                owner_ids=_owners(item),
                affected_refs=tuple(dict.fromkeys((task_ref, *expected_refs))),
                support=(item,),
                now=now,
            )
        )
    return tuple(findings)


def pr_review_or_ci_rule(
    evidence: Sequence[Evidence],
    now: datetime,
    config: RiskRuleConfig,
) -> tuple[RiskFinding, ...]:
    current = ensure_utc(now)
    findings: list[RiskFinding] = []
    for item in evidence:
        if item.type != EvidenceType.PULL_REQUEST:
            continue
        pr_ref = str(item.metadata.get("pr_id") or item.metadata.get("number") or item.source.source_id)
        opened_at = _datetime_value(item.metadata.get("opened_at")) or item.observed_at
        wait_time = current - ensure_utc(opened_at)
        approvals = _int_value(item.metadata.get("approvals"), default=0)
        required = _int_value(item.metadata.get("approvals_required"), default=1)
        review_status = _normalized(item.metadata.get("review_status"))
        waiting_review = (
            wait_time > config.review_wait_threshold
            and approvals < required
            and review_status not in {"approved", "merged", "closed"}
        )
        ci_status = _normalized(item.metadata.get("ci_status"))
        task_status = _normalized(item.metadata.get("task_status"))
        release_ready = item.metadata.get("release_ready") is True or (
            task_status in _RELEASE_READY_STATUSES
        )
        failed_release_ci = ci_status in _FAILED_CI and release_ready
        if not waiting_review and not failed_release_ci:
            continue
        reasons: list[str] = []
        if waiting_review:
            reasons.append(f"超过 {config.review_wait_threshold} 无足够评审")
        if failed_release_ci:
            reasons.append("CI 失败但关联任务仍处于准备发布状态")
        severity = (
            RiskSeverity.HIGH
            if failed_release_ci or wait_time >= config.review_high_after
            else RiskSeverity.MEDIUM
        )
        findings.append(
            _finding(
                project=item.project,
                risk_type=RiskType.PR_REVIEW_OR_CI,
                severity=severity,
                title=f"PR {pr_ref} 存在评审或 CI 风险",
                summary="；".join(reasons) + "。",
                primary_ref=pr_ref,
                owner_ids=_owners(item),
                affected_refs=tuple(
                    dict.fromkeys((pr_ref, *_string_tuple(item.metadata.get("task_id"))))
                ),
                support=(item,),
                now=current,
            )
        )
    return tuple(findings)


def chat_blocker_rule(
    evidence: Sequence[Evidence],
    now: datetime,
    config: RiskRuleConfig,
) -> tuple[RiskFinding, ...]:
    del config
    findings: list[RiskFinding] = []
    for item in evidence:
        if not _is_chat_message(item):
            continue
        content = item.content.casefold()
        explicit = item.metadata.get("blocker_confirmed") is True or any(
            term in content for term in _BLOCKER_TERMS
        )
        resolved = item.metadata.get("blocker_resolved") is True or any(
            term in content for term in _RESOLVED_TERMS
        )
        if not explicit or resolved:
            continue
        task_refs = _string_tuple(
            item.metadata.get("task_id") or item.metadata.get("related_task_ids")
        )
        message_ref = str(item.metadata.get("message_id") or item.source.source_id)
        primary_ref = task_refs[0] if task_refs else message_ref
        findings.append(
            _finding(
                project=item.project,
                risk_type=RiskType.CHAT_BLOCKER,
                severity=(
                    RiskSeverity.HIGH
                    if item.metadata.get("severity") == "high"
                    else RiskSeverity.MEDIUM
                ),
                title=f"群聊出现明确阻塞：{primary_ref}",
                summary="群聊消息包含明确阻塞表达，并已关联当前项目、任务或责任人。",
                primary_ref=primary_ref,
                owner_ids=_owners(item),
                affected_refs=tuple(dict.fromkeys((*task_refs, message_ref))),
                support=(item,),
                now=now,
            )
        )
    return tuple(findings)


def _finding(
    *,
    project: ProjectRef,
    risk_type: RiskType,
    severity: RiskSeverity,
    title: str,
    summary: str,
    primary_ref: str,
    owner_ids: tuple[str, ...],
    affected_refs: tuple[str, ...],
    support: Sequence[Evidence],
    now: datetime,
    due_at: datetime | None = None,
) -> RiskFinding:
    canonical_owners = tuple(sorted(set(owner_ids)))
    observed = ensure_utc(now)
    return RiskFinding(
        risk_id=stable_risk_id(project, risk_type, primary_ref, canonical_owners),
        project=project,
        risk_type=risk_type,
        severity=severity,
        title=title,
        summary=summary,
        primary_ref=primary_ref,
        owner_ids=canonical_owners,
        affected_refs=tuple(dict.fromkeys(affected_refs)),
        evidence_ids=tuple(dict.fromkeys(item.id for item in support)),
        evidence_signature=evidence_signature(
            [(item.id, item.content_hash) for item in support]
        ),
        detected_at=observed,
        last_seen_at=observed,
        state=RiskState.OPEN,
        routing_queue=None if canonical_owners else "project_owners",
        due_at=due_at,
    )


def _tasks(evidence: Sequence[Evidence]) -> Iterable[Evidence]:
    return (
        item
        for item in evidence
        if item.type == EvidenceType.TASK and item.metadata.get("kind", "task") == "task"
    )


def _task_ref(item: Evidence) -> str:
    return str(item.metadata.get("task_id") or item.source.source_id)


def _status(item: Evidence) -> str:
    return _normalized(item.metadata.get("status"))


def _normalized(value: object) -> str:
    return str(value or "").strip().casefold().replace(" ", "_").replace("-", "_")


def _owners(item: Evidence) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("owner_ids", "assignee_ids", "reviewer_ids"):
        values.extend(_string_tuple(item.metadata.get(key)))
    for key in ("assignee", "owner_id", "owner_user_id", "author_id"):
        values.extend(_string_tuple(item.metadata.get(key)))
    return tuple(sorted(set(value for value in values if value)))


def _merge_owners(items: Iterable[Evidence]) -> tuple[str, ...]:
    return tuple(sorted({owner for item in items for owner in _owners(item)}))


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _datetime_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _int_value(value: object, *, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _has_meaningful_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return bool(value)
    return value is not None


def _is_requirement(item: Evidence) -> bool:
    kind = _normalized(item.metadata.get("kind"))
    return kind in {"requirement", "prd", "requirement_document"} or bool(
        item.metadata.get("requirement_id")
    )


def _code_refs(evidence: Sequence[Evidence]) -> set[str]:
    refs: set[str] = set()
    for item in evidence:
        if item.type not in {EvidenceType.COMMIT, EvidenceType.PULL_REQUEST, EvidenceType.CODE}:
            continue
        refs.add(item.source.source_id)
        for key in ("commit_sha", "pr_id", "number", "branch", "ref"):
            refs.update(_string_tuple(item.metadata.get(key)))
    return refs


def _is_chat_message(item: Evidence) -> bool:
    kind = _normalized(item.metadata.get("kind"))
    system = item.source.system.casefold()
    return kind in {"chat", "chat_message", "group_message", "meeting_message"} or (
        "chat" in system or "feishu_im" in system
    )
