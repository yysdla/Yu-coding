"""Run Feishu doc sync across registered projects without affecting chat paths."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from project_lens.application.feishu_doc_sync import (
    FeishuDocSyncResult,
    FeishuDocumentSyncService,
)
from project_lens.context.bootstrap import LocalProjectRegistration
from project_lens.runtime.lifecycle import LifecycleBus, LifecycleEventType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeishuDocSyncRunSummary:
    project_count: int
    requested: int
    fetched: int
    indexed: int
    skipped_unchanged: int
    failed: int
    results: tuple[FeishuDocSyncResult, ...] = ()
    errors: tuple[str, ...] = ()


def sync_registered_projects(
    service: FeishuDocumentSyncService,
    registrations: tuple[LocalProjectRegistration, ...],
    *,
    lifecycle: LifecycleBus | None = None,
) -> FeishuDocSyncRunSummary:
    """Sync all projects that declare feishu_doc_tokens.

    Individual project failures are logged and collected; they never raise.
    """

    bus = lifecycle or LifecycleBus()
    if not service.available:
        return FeishuDocSyncRunSummary(
            project_count=0,
            requested=0,
            fetched=0,
            indexed=0,
            skipped_unchanged=0,
            failed=0,
            errors=("Feishu document API client is not configured",),
        )

    results: list[FeishuDocSyncResult] = []
    errors: list[str] = []
    for registration in registrations:
        tokens = registration.sources.feishu_doc_tokens
        if not tokens:
            continue
        bus.emit(
            LifecycleEventType.DOC_SYNC_STARTED,
            project=registration.project,
            payload={
                "token_count": len(tokens),
                "project_id": registration.project.project_id,
            },
        )
        try:
            result = service.sync_tokens(
                project=registration.project,
                access_scope=registration.access_scope,
                doc_tokens=tokens,
            )
            results.append(result)
            bus.emit(
                LifecycleEventType.DOC_SYNC_COMPLETED,
                project=registration.project,
                payload={
                    "requested": result.requested,
                    "indexed": result.indexed,
                    "skipped_unchanged": result.skipped_unchanged,
                    "failed_count": len(result.failed),
                },
            )
            if result.failed:
                logger.warning(
                    "feishu doc sync completed with failures project=%s failed=%s",
                    registration.project.project_id,
                    list(result.failed),
                )
            else:
                logger.info(
                    "feishu doc sync completed project=%s indexed=%s skipped=%s",
                    registration.project.project_id,
                    result.indexed,
                    result.skipped_unchanged,
                )
        except Exception as exc:  # noqa: BLE001 - never break startup/scheduler
            message = f"{registration.project.project_id}: {exc}"
            logger.exception("feishu doc sync project failed project=%s", registration.project.project_id)
            errors.append(message)
            bus.emit(
                LifecycleEventType.DOC_SYNC_COMPLETED,
                project=registration.project,
                payload={"failed_count": 1, "error": message},
            )

    return FeishuDocSyncRunSummary(
        project_count=len(results),
        requested=sum(item.requested for item in results),
        fetched=sum(item.fetched for item in results),
        indexed=sum(item.indexed for item in results),
        skipped_unchanged=sum(item.skipped_unchanged for item in results),
        failed=sum(len(item.failed) for item in results) + len(errors),
        results=tuple(results),
        errors=tuple(errors),
    )
