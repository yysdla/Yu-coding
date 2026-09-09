"""Route risk notifications without weakening ProjectSpace visibility policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_feedback_service import RiskFeedbackService
from project_lens.application.risk_feedback_store import RiskFeedbackStore
from project_lens.application.daily_risk_digest import DailyRiskDigest, render_daily_risk_digest
from project_lens.domain.models import ProjectRef
from project_lens.domain.risk import RiskFinding, RiskSeverity, RiskState, ensure_utc
from project_lens.domain.risk_feedback import RiskNotificationKind, RiskNotificationRecord
from project_lens.integrations.feishu.adapter import FeishuMessenger
from project_lens.integrations.feishu.cards import render_risk_card
from project_lens.project_space.models import ProjectSpace
from project_lens.project_space.policies import ProjectRuntimeContextResolver, RoleKind


class ProjectSpaceLookup(Protocol):
    def require(self, tenant_id: str, project_id: str) -> ProjectSpace: ...


@dataclass(frozen=True)
class RiskDispatchResult:
    sent: tuple[RiskNotificationRecord, ...] = ()
    skipped: tuple[str, ...] = ()


class ProjectRiskRecipientDirectory:
    def __init__(self, registry: ProjectSpaceLookup) -> None:
        self._registry = registry

    def project_owner_ids(self, project: ProjectRef) -> tuple[str, ...]:
        space = self._registry.require(project.tenant_id, project.project_id)
        return tuple(
            sorted(
                policy.actor_id
                for policy in space.role_policies
                if policy.role == RoleKind.MANAGER
            )
        )

    def primary_group_chat_id(self, project: ProjectRef) -> str | None:
        space = self._registry.require(project.tenant_id, project.project_id)
        return space.feishu_chat_bindings[0].chat_id if space.feishu_chat_bindings else None


class RiskNotificationService:
    def __init__(
        self,
        *,
        messenger: FeishuMessenger,
        feedback_store: RiskFeedbackStore,
        risk_engine: RiskEngine,
        recipient_directory: ProjectRiskRecipientDirectory | None = None,
        runtime_context_resolver: ProjectRuntimeContextResolver | None = None,
    ) -> None:
        self._messenger = messenger
        self._feedback_store = feedback_store
        self._risk_engine = risk_engine
        self._recipients = recipient_directory
        self._resolver = runtime_context_resolver

    async def dispatch_immediate(
        self,
        findings: tuple[RiskFinding, ...],
        *,
        notify_risk_ids: tuple[str, ...] | None = None,
        now: datetime | None = None,
    ) -> RiskDispatchResult:
        current = ensure_utc(now or datetime.now(timezone.utc))
        allowed_ids = set(notify_risk_ids) if notify_risk_ids is not None else None
        sent: list[RiskNotificationRecord] = []
        skipped: list[str] = []
        for finding in findings:
            if allowed_ids is not None and finding.risk_id not in allowed_ids:
                skipped.append(f"{finding.risk_id}:not_changed")
                continue
            if finding.severity != RiskSeverity.HIGH:
                skipped.append(f"{finding.risk_id}:digest_only")
                continue
            if finding.state != RiskState.OPEN:
                skipped.append(f"{finding.risk_id}:state={finding.state.value}")
                continue
            recipients = finding.owner_ids or self._project_owner_ids(finding.project)
            if not recipients:
                skipped.append(f"{finding.risk_id}:no_project_owner")
                continue
            for recipient in recipients:
                key = (
                    f"risk:{finding.risk_id}:immediate:{finding.evidence_signature}:"
                    f"{finding.severity.value}:{recipient}"
                )
                record = RiskNotificationRecord(
                    idempotency_key=key,
                    risk_id=finding.risk_id,
                    project=finding.project,
                    kind=RiskNotificationKind.IMMEDIATE,
                    recipient_id=recipient,
                    channel_type="p2p",
                    sent_at=current,
                    evidence_refs=finding.evidence_ids,
                )
                if not self._feedback_store.mark_notification(record):
                    skipped.append(f"{finding.risk_id}:already_sent:{recipient}")
                    continue
                try:
                    await self._messenger.post_user_card(recipient, render_risk_card(finding))
                except Exception:
                    self._feedback_store.delete_notification(key)
                    raise
                sent.append(record)
        return RiskDispatchResult(sent=tuple(sent), skipped=tuple(skipped))

    async def dispatch_due_snoozes(
        self,
        project: ProjectRef,
        feedback_service: RiskFeedbackService,
        *,
        now: datetime | None = None,
    ) -> RiskDispatchResult:
        current = ensure_utc(now or datetime.now(timezone.utc))
        sent: list[RiskNotificationRecord] = []
        skipped: list[str] = []
        for feedback in feedback_service.due_snoozes(project, now=current):
            finding = self._risk_engine.get(feedback.risk_id)
            if finding is None or finding.state != RiskState.SNOOZED:
                skipped.append(f"{feedback.risk_id}:not_snoozed")
                continue
            key = f"risk:{feedback.risk_id}:snooze_due:{feedback.feedback_id}"
            recipients = finding.owner_ids or self._project_owner_ids(project)
            if not recipients:
                skipped.append(f"{feedback.risk_id}:no_recipient")
                continue
            marker = RiskNotificationRecord(
                idempotency_key=key,
                risk_id=finding.risk_id,
                project=project,
                kind=RiskNotificationKind.SNOOZE_DUE,
                recipient_id="system",
                channel_type="p2p",
                sent_at=current,
                evidence_refs=finding.evidence_ids,
            )
            if not self._feedback_store.mark_notification(marker):
                skipped.append(f"{feedback.risk_id}:snooze_already_sent")
                continue
            reopened = self._risk_engine.transition(
                finding.risk_id,
                RiskState.OPEN,
                actor_id="system:snooze_due",
                now=current,
            )
            try:
                for recipient in recipients:
                    recipient_key = f"{key}:{recipient}"
                    record = RiskNotificationRecord(
                        idempotency_key=recipient_key,
                        risk_id=finding.risk_id,
                        project=project,
                        kind=RiskNotificationKind.SNOOZE_DUE,
                        recipient_id=recipient,
                        channel_type="p2p",
                        sent_at=current,
                        evidence_refs=finding.evidence_ids,
                    )
                    if not self._feedback_store.mark_notification(record):
                        continue
                    await self._messenger.post_user_card(recipient, render_risk_card(reopened))
                    sent.append(record)
            except Exception:
                self._risk_engine.transition(
                    finding.risk_id,
                    RiskState.SNOOZED,
                    actor_id="system:snooze_retry",
                    now=current,
                )
                self._feedback_store.delete_notification(key)
                self._feedback_store.delete_notification(recipient_key)
                raise
        return RiskDispatchResult(sent=tuple(sent), skipped=tuple(skipped))

    async def escalate_to_group(
        self,
        finding: RiskFinding,
        *,
        group_chat_id: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> RiskNotificationRecord:
        if self._resolver is None:
            raise PermissionError("group escalation requires runtime context resolver")
        resolved = self._resolver.resolve(
            tenant_id=finding.project.tenant_id,
            project_id=finding.project.project_id,
            chat_id=group_chat_id,
            user_id=actor_id,
            chat_type="group",
            identity_source="risk_group_escalation",
        )
        if not resolved.effective_scope.readable_sources:
            raise PermissionError("group scope cannot read any project risk sources")
        key = f"risk:{finding.risk_id}:group:{group_chat_id}:{finding.evidence_signature}"
        if self._feedback_store.notification_seen(key):
            existing = next(
                item
                for item in self._feedback_store.list_notifications(finding.project)
                if item.idempotency_key == key
            )
            return existing
        record = RiskNotificationRecord(
            idempotency_key=key,
            risk_id=finding.risk_id,
            project=finding.project,
            kind=RiskNotificationKind.GROUP_ESCALATION,
            recipient_id=group_chat_id,
            channel_type="group",
            sent_at=ensure_utc(now or datetime.now(timezone.utc)),
            evidence_refs=finding.evidence_ids,
        )
        if not self._feedback_store.mark_notification(record):
            return next(
                item
                for item in self._feedback_store.list_notifications(finding.project)
                if item.idempotency_key == key
            )
        try:
            await self._messenger.post_card(
                group_chat_id,
                render_risk_card(finding, group_safe=True),
            )
        except Exception:
            self._feedback_store.delete_notification(key)
            raise
        return record

    async def request_help(
        self,
        finding: RiskFinding,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> RiskNotificationRecord:
        if self._recipients is None:
            raise ValueError("request help requires a project recipient directory")
        group_chat_id = self._recipients.primary_group_chat_id(finding.project)
        if group_chat_id is None:
            raise ValueError("project has no Feishu group binding for escalation")
        return await self.escalate_to_group(
            finding,
            group_chat_id=group_chat_id,
            actor_id=actor_id,
            now=now,
        )

    async def dispatch_daily_digest(
        self,
        digest: DailyRiskDigest,
        *,
        now: datetime | None = None,
    ) -> RiskDispatchResult:
        current = ensure_utc(now or datetime.now(timezone.utc))
        sent: list[RiskNotificationRecord] = []
        skipped: list[str] = []
        for section in digest.owner_sections:
            recipients = (
                self._project_owner_ids(digest.project)
                if section.owner_id == "project_owners"
                else (section.owner_id,)
            )
            if not recipients:
                skipped.append(f"digest:{section.owner_id}:no_recipient")
                continue
            owner_digest = DailyRiskDigest(
                project=digest.project,
                digest_date=digest.digest_date,
                owner_sections=(section,),
                resolved_count=digest.resolved_count,
                dismissed_count=digest.dismissed_count,
            )
            for recipient in recipients:
                key = f"digest:{digest.project.tenant_id}:{digest.project.project_id}:{digest.digest_date}:{recipient}"
                record = RiskNotificationRecord(
                    idempotency_key=key,
                    risk_id=None,
                    project=digest.project,
                    kind=RiskNotificationKind.DAILY_DIGEST,
                    recipient_id=recipient,
                    channel_type="p2p",
                    sent_at=current,
                )
                if not self._feedback_store.mark_notification(record):
                    skipped.append(f"digest:{recipient}:already_sent")
                    continue
                try:
                    await self._messenger.post_user_card(
                        recipient,
                        render_daily_risk_digest(owner_digest),
                    )
                except Exception:
                    self._feedback_store.delete_notification(key)
                    raise
                sent.append(record)
        return RiskDispatchResult(sent=tuple(sent), skipped=tuple(skipped))

    def _project_owner_ids(self, project: ProjectRef) -> tuple[str, ...]:
        if self._recipients is None:
            return ()
        return self._recipients.project_owner_ids(project)
