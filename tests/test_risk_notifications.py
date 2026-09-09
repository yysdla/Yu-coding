from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_feedback_service import RiskFeedbackService
from project_lens.application.risk_feedback_store import InMemoryRiskFeedbackStore
from project_lens.application.risk_notification_service import RiskNotificationService
from project_lens.application.daily_risk_digest import DailyRiskDigestService
from project_lens.application.risk_store import InMemoryRiskStore
from project_lens.domain.models import ProjectRef
from project_lens.domain.risk import (
    RiskFinding,
    RiskSeverity,
    RiskState,
    RiskType,
    evidence_signature,
    stable_risk_id,
)
from project_lens.integrations.feishu.adapter import RecordingFeishuMessenger


NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone.utc)
PROJECT = ProjectRef(tenant_id="tenant", project_id="project")


def finding(
    *,
    severity: RiskSeverity = RiskSeverity.HIGH,
    owners: tuple[str, ...] = ("owner-1",),
    state: RiskState = RiskState.OPEN,
) -> RiskFinding:
    risk_id = stable_risk_id(PROJECT, RiskType.OVERDUE_TASK, "TASK-1", owners)
    return RiskFinding(
        risk_id=risk_id,
        project=PROJECT,
        risk_type=RiskType.OVERDUE_TASK,
        severity=severity,
        title="任务 TASK-1 已逾期",
        summary="任务已超过截止时间。",
        primary_ref="TASK-1",
        owner_ids=owners,
        affected_refs=("TASK-1",),
        evidence_ids=(__import__("uuid").uuid4(),),
        evidence_signature=evidence_signature([]),
        detected_at=NOW,
        last_seen_at=NOW,
        state=state,
        routing_queue=None if owners else "project_owners",
    )


def test_high_risk_is_sent_only_as_owner_private_message() -> None:
    messenger = RecordingFeishuMessenger()
    feedback_store = InMemoryRiskFeedbackStore()
    risk = finding()
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=feedback_store,
        risk_engine=RiskEngine(InMemoryRiskStore()),
    )

    result = asyncio.run(service.dispatch_immediate((risk,), now=NOW))

    assert len(result.sent) == 1
    assert messenger.messages[0].chat_id == "owner-1"
    assert messenger.messages[0].message_type == "interactive:p2p"


def test_medium_risk_is_digest_only_and_not_sent_immediately() -> None:
    messenger = RecordingFeishuMessenger()
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=InMemoryRiskFeedbackStore(),
        risk_engine=RiskEngine(InMemoryRiskStore()),
    )

    result = asyncio.run(
        service.dispatch_immediate((finding(severity=RiskSeverity.MEDIUM),), now=NOW)
    )

    assert result.sent == ()
    assert messenger.messages == []
    assert any("digest_only" in item for item in result.skipped)


def test_same_high_risk_notification_is_idempotent() -> None:
    messenger = RecordingFeishuMessenger()
    feedback_store = InMemoryRiskFeedbackStore()
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=feedback_store,
        risk_engine=RiskEngine(InMemoryRiskStore()),
    )
    risk = finding()

    asyncio.run(service.dispatch_immediate((risk,), now=NOW))
    second = asyncio.run(service.dispatch_immediate((risk,), now=NOW + timedelta(minutes=5)))

    assert second.sent == ()
    assert len(messenger.messages) == 1


def test_acknowledged_risk_is_not_repeated_immediately() -> None:
    messenger = RecordingFeishuMessenger()
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=InMemoryRiskFeedbackStore(),
        risk_engine=RiskEngine(InMemoryRiskStore()),
    )

    result = asyncio.run(
        service.dispatch_immediate((finding(state=RiskState.ACKNOWLEDGED),), now=NOW)
    )

    assert result.sent == ()
    assert messenger.messages == []


class _Resolver:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.calls = 0

    def resolve(self, **kwargs):
        del kwargs
        self.calls += 1
        if not self.allowed:
            raise PermissionError("group denied")
        scope = type("Scope", (), {"readable_sources": ("knowledge/",)})()
        return type("Resolved", (), {"effective_scope": scope})()


def test_group_escalation_recomputes_visibility_policy() -> None:
    messenger = RecordingFeishuMessenger()
    resolver = _Resolver(allowed=True)
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=InMemoryRiskFeedbackStore(),
        risk_engine=RiskEngine(InMemoryRiskStore()),
        runtime_context_resolver=resolver,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.escalate_to_group(
            finding(),
            group_chat_id="group-1",
            actor_id="owner-1",
            now=NOW,
        )
    )

    assert resolver.calls == 1
    assert messenger.messages[0].chat_id == "group-1"
    assert "群内不展示来源正文" in str(messenger.messages[0].content)


def test_group_escalation_denies_when_recomputed_scope_denies() -> None:
    resolver = _Resolver(allowed=False)
    service = RiskNotificationService(
        messenger=RecordingFeishuMessenger(),
        feedback_store=InMemoryRiskFeedbackStore(),
        risk_engine=RiskEngine(InMemoryRiskStore()),
        runtime_context_resolver=resolver,  # type: ignore[arg-type]
    )

    with pytest.raises(PermissionError):
        asyncio.run(
            service.escalate_to_group(
                finding(),
                group_chat_id="group-1",
                actor_id="owner-1",
                now=NOW,
            )
        )


def test_snooze_due_reopens_and_notifies_only_once() -> None:
    risk_store = InMemoryRiskStore()
    engine = RiskEngine(risk_store)
    risk = finding()
    risk_store.save(risk)
    feedback_store = InMemoryRiskFeedbackStore()
    feedback_service = RiskFeedbackService(risk_engine=engine, store=feedback_store)
    feedback_service.submit(
        risk_id=risk.risk_id,
        actor_id="owner-1",
        action="snooze",
        idempotency_key="snooze-1",
        snoozed_until=NOW + timedelta(hours=1),
        created_at=NOW,
    )
    messenger = RecordingFeishuMessenger()
    notification_service = RiskNotificationService(
        messenger=messenger,
        feedback_store=feedback_store,
        risk_engine=engine,
    )

    first = asyncio.run(
        notification_service.dispatch_due_snoozes(
            PROJECT,
            feedback_service,
            now=NOW + timedelta(hours=2),
        )
    )
    second = asyncio.run(
        notification_service.dispatch_due_snoozes(
            PROJECT,
            feedback_service,
            now=NOW + timedelta(hours=3),
        )
    )

    assert len(first.sent) == 1
    assert second.sent == ()
    assert len(messenger.messages) == 1
    assert engine.get(risk.risk_id).state == RiskState.OPEN  # type: ignore[union-attr]


class _Recipients:
    def project_owner_ids(self, project):
        del project
        return ("manager-1",)

    def primary_group_chat_id(self, project):
        del project
        return "group-1"


def test_unowned_high_risk_routes_to_project_manager_private_message() -> None:
    messenger = RecordingFeishuMessenger()
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=InMemoryRiskFeedbackStore(),
        risk_engine=RiskEngine(InMemoryRiskStore()),
        recipient_directory=_Recipients(),  # type: ignore[arg-type]
    )
    result = asyncio.run(service.dispatch_immediate((finding(owners=()),), now=NOW))
    assert len(result.sent) == 1
    assert messenger.messages[0].chat_id == "manager-1"
    assert messenger.messages[0].message_type == "interactive:p2p"


def test_daily_digest_is_delivered_once_per_owner_per_day() -> None:
    messenger = RecordingFeishuMessenger()
    feedback_store = InMemoryRiskFeedbackStore()
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=feedback_store,
        risk_engine=RiskEngine(InMemoryRiskStore()),
    )
    digest = DailyRiskDigestService(feedback_store).build(
        PROJECT,
        (finding(severity=RiskSeverity.MEDIUM),),
        now=NOW,
    )
    first = asyncio.run(service.dispatch_daily_digest(digest, now=NOW))
    second = asyncio.run(service.dispatch_daily_digest(digest, now=NOW + timedelta(hours=1)))
    assert len(first.sent) == 1
    assert second.sent == ()
    assert len(messenger.messages) == 1
    assert "每日风险摘要" in str(messenger.messages[0].content)


def test_request_help_uses_bound_group_and_rechecks_scope() -> None:
    messenger = RecordingFeishuMessenger()
    resolver = _Resolver(allowed=True)
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=InMemoryRiskFeedbackStore(),
        risk_engine=RiskEngine(InMemoryRiskStore()),
        recipient_directory=_Recipients(),  # type: ignore[arg-type]
        runtime_context_resolver=resolver,  # type: ignore[arg-type]
    )
    record = asyncio.run(service.request_help(finding(), actor_id="owner-1", now=NOW))
    assert resolver.calls == 1
    assert record.recipient_id == "group-1"
    assert record.channel_type == "group"


class _FailOnceMessenger(RecordingFeishuMessenger):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    async def post_user_card(self, open_id, card) -> None:
        if not self.failed:
            self.failed = True
            raise RuntimeError("temporary send failure")
        await super().post_user_card(open_id, card)


def test_failed_send_releases_idempotency_claim_for_retry() -> None:
    messenger = _FailOnceMessenger()
    feedback_store = InMemoryRiskFeedbackStore()
    service = RiskNotificationService(
        messenger=messenger,
        feedback_store=feedback_store,
        risk_engine=RiskEngine(InMemoryRiskStore()),
    )
    risk = finding()
    with pytest.raises(RuntimeError):
        asyncio.run(service.dispatch_immediate((risk,), now=NOW))

    retry = asyncio.run(service.dispatch_immediate((risk,), now=NOW + timedelta(minutes=1)))

    assert len(retry.sent) == 1
    assert len(messenger.messages) == 1
