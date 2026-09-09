from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

from project_lens.application.risk_rules import (
    RiskRuleConfig,
    blocked_dependency_rule,
    chat_blocker_rule,
    code_status_mismatch_rule,
    overdue_task_rule,
    pr_review_or_ci_rule,
    unsynced_requirement_rule,
)
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.domain.risk import RiskSeverity, RiskType


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
PROJECT = ProjectRef(tenant_id="tenant-1", project_id="project-1", service="api")
CONFIG = RiskRuleConfig()


def evidence(
    source_id: str,
    *,
    kind: EvidenceType = EvidenceType.TASK,
    observed_at: datetime = NOW,
    content: str = "fixture evidence",
    metadata: dict[str, object] | None = None,
    system: str = "fixture",
) -> Evidence:
    return Evidence(
        type=kind,
        project=PROJECT,
        source=SourceRef(system=system, source_id=source_id),
        content=content,
        observed_at=observed_at,
        access_scope="project:read",
        content_hash=sha256(content.encode()).hexdigest(),
        metadata=metadata or {},
    )


def test_overdue_task_rule_hits_and_cites_task() -> None:
    task = evidence(
        "TASK-1",
        metadata={
            "kind": "task",
            "task_id": "TASK-1",
            "status": "in_progress",
            "due_at": (NOW - timedelta(days=8)).isoformat(),
            "owner_ids": ["u2", "u1"],
        },
    )

    findings = overdue_task_rule((task,), NOW, CONFIG)

    assert len(findings) == 1
    assert findings[0].risk_type == RiskType.OVERDUE_TASK
    assert findings[0].severity == RiskSeverity.HIGH
    assert findings[0].owner_ids == ("u1", "u2")
    assert findings[0].evidence_ids == (task.id,)


def test_overdue_task_rule_does_not_hit_at_deadline_boundary() -> None:
    task = evidence(
        "TASK-1",
        metadata={"kind": "task", "status": "open", "due_at": NOW.isoformat()},
    )
    assert overdue_task_rule((task,), NOW, CONFIG) == ()


def test_overdue_task_rule_ignores_completed_task() -> None:
    task = evidence(
        "TASK-1",
        metadata={
            "kind": "task",
            "status": "done",
            "due_at": (NOW - timedelta(days=1)).isoformat(),
        },
    )
    assert overdue_task_rule((task,), NOW, CONFIG) == ()


def test_blocked_dependency_rule_hits_with_both_evidence_items() -> None:
    dependency = evidence(
        "TASK-DEP",
        metadata={"kind": "task", "task_id": "TASK-DEP", "status": "blocked"},
    )
    task = evidence(
        "TASK-2",
        metadata={
            "kind": "task",
            "task_id": "TASK-2",
            "status": "in_progress",
            "dependency_ids": ["TASK-DEP"],
        },
    )

    findings = blocked_dependency_rule((task, dependency), NOW, CONFIG)

    assert len(findings) == 1
    assert set(findings[0].evidence_ids) == {task.id, dependency.id}


def test_blocked_dependency_rule_ignores_completed_affected_task() -> None:
    task = evidence(
        "TASK-2",
        metadata={"kind": "task", "status": "done", "dependency_status": "blocked"},
    )
    assert blocked_dependency_rule((task,), NOW, CONFIG) == ()


def test_blocked_dependency_rule_ignores_healthy_dependency() -> None:
    dependency = evidence(
        "TASK-DEP",
        metadata={"kind": "task", "task_id": "TASK-DEP", "status": "done"},
    )
    task = evidence(
        "TASK-2",
        metadata={
            "kind": "task",
            "task_id": "TASK-2",
            "status": "open",
            "dependency_ids": ["TASK-DEP"],
        },
    )
    assert blocked_dependency_rule((task, dependency), NOW, CONFIG) == ()


def test_unsynced_requirement_rule_hits_when_requirement_is_newer() -> None:
    task = evidence(
        "TASK-3",
        observed_at=NOW - timedelta(hours=2),
        metadata={"kind": "task", "task_id": "TASK-3", "status": "in_progress"},
    )
    requirement = evidence(
        "REQ-1",
        kind=EvidenceType.DOCUMENT,
        metadata={
            "kind": "requirement",
            "requirement_id": "REQ-1",
            "linked_task_ids": ["TASK-3"],
            "acceptance_criteria": ["returns 200"],
        },
    )

    findings = unsynced_requirement_rule((task, requirement), NOW, CONFIG)

    assert len(findings) == 1
    assert set(findings[0].evidence_ids) == {task.id, requirement.id}


def test_unsynced_requirement_rule_does_not_hit_equal_timestamps() -> None:
    task = evidence(
        "TASK-3",
        metadata={"kind": "task", "task_id": "TASK-3", "status": "open"},
    )
    requirement = evidence(
        "REQ-1",
        kind=EvidenceType.DOCUMENT,
        metadata={
            "kind": "requirement",
            "linked_task_ids": ["TASK-3"],
            "acceptance_criteria": ["defined"],
        },
    )
    assert unsynced_requirement_rule((task, requirement), NOW, CONFIG) == ()


def test_unsynced_requirement_rule_ignores_complete_requirement_metadata() -> None:
    requirement = evidence(
        "REQ-1",
        kind=EvidenceType.DOCUMENT,
        metadata={
            "kind": "requirement",
            "acceptance_criteria_required": True,
            "acceptance_criteria": ["defined"],
        },
    )
    assert unsynced_requirement_rule((requirement,), NOW, CONFIG) == ()


def test_code_status_mismatch_rule_hits_missing_commit() -> None:
    task = evidence(
        "TASK-4",
        metadata={
            "kind": "task",
            "task_id": "TASK-4",
            "status": "done",
            "related_commit_sha": "abc123",
        },
    )

    findings = code_status_mismatch_rule((task,), NOW, CONFIG)

    assert len(findings) == 1
    assert findings[0].risk_type == RiskType.CODE_STATUS_MISMATCH
    assert findings[0].severity == RiskSeverity.HIGH


def test_code_status_mismatch_rule_accepts_matching_commit() -> None:
    task = evidence(
        "TASK-4",
        metadata={
            "kind": "task",
            "status": "done",
            "related_commit_sha": "abc123",
        },
    )
    commit = evidence(
        "abc123",
        kind=EvidenceType.COMMIT,
        metadata={"commit_sha": "abc123"},
    )
    assert code_status_mismatch_rule((task, commit), NOW, CONFIG) == ()


def test_code_status_mismatch_rule_ignores_unstarted_task() -> None:
    task = evidence(
        "TASK-4",
        metadata={"kind": "task", "status": "open", "requires_code": True},
    )
    assert code_status_mismatch_rule((task,), NOW, CONFIG) == ()


def test_pr_review_rule_hits_after_threshold() -> None:
    pull_request = evidence(
        "PR-5",
        kind=EvidenceType.PULL_REQUEST,
        metadata={
            "pr_id": "PR-5",
            "opened_at": (NOW - timedelta(hours=25)).isoformat(),
            "review_status": "pending",
            "approvals": 0,
            "approvals_required": 1,
        },
    )
    findings = pr_review_or_ci_rule((pull_request,), NOW, CONFIG)
    assert len(findings) == 1
    assert findings[0].risk_type == RiskType.PR_REVIEW_OR_CI


def test_pr_review_rule_does_not_hit_at_threshold_boundary() -> None:
    pull_request = evidence(
        "PR-5",
        kind=EvidenceType.PULL_REQUEST,
        metadata={
            "opened_at": (NOW - timedelta(hours=24)).isoformat(),
            "review_status": "pending",
        },
    )
    assert pr_review_or_ci_rule((pull_request,), NOW, CONFIG) == ()


def test_pr_ci_rule_ignores_failed_ci_when_task_is_not_release_ready() -> None:
    pull_request = evidence(
        "PR-5",
        kind=EvidenceType.PULL_REQUEST,
        metadata={"ci_status": "failed", "task_status": "in_progress"},
    )
    assert pr_review_or_ci_rule((pull_request,), NOW, CONFIG) == ()


def test_chat_blocker_rule_hits_explicit_project_message() -> None:
    message = evidence(
        "MSG-6",
        kind=EvidenceType.DOCUMENT,
        content="TASK-6 当前阻塞，接口权限没有开通，无法继续。",
        system="feishu_im",
        metadata={"kind": "chat_message", "message_id": "MSG-6", "task_id": "TASK-6"},
    )
    findings = chat_blocker_rule((message,), NOW, CONFIG)
    assert len(findings) == 1
    assert findings[0].primary_ref == "TASK-6"
    assert findings[0].evidence_ids == (message.id,)


def test_chat_blocker_rule_ignores_resolved_blocker_message() -> None:
    message = evidence(
        "MSG-6",
        kind=EvidenceType.DOCUMENT,
        content="TASK-6 已解决，不再阻塞。",
        system="feishu_im",
        metadata={"kind": "chat_message"},
    )
    assert chat_blocker_rule((message,), NOW, CONFIG) == ()


def test_chat_blocker_rule_ignores_non_blocking_chat() -> None:
    message = evidence(
        "MSG-6",
        kind=EvidenceType.DOCUMENT,
        content="今天已经完成联调，明天继续验收。",
        system="feishu_im",
        metadata={"kind": "chat_message"},
    )
    assert chat_blocker_rule((message,), NOW, CONFIG) == ()
