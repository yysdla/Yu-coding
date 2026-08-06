"""Background startup/interval Feishu document sync (optional, best-effort)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from project_lens.application.feishu_doc_sync import FeishuDocumentSyncService
from project_lens.application.feishu_doc_sync_runner import (
    FeishuDocSyncRunSummary,
    sync_registered_projects,
)
from project_lens.context.bootstrap import LocalProjectRegistration
from project_lens.runtime.lifecycle import LifecycleBus

logger = logging.getLogger(__name__)


class FeishuDocSyncScheduler:
    """Daemon thread scheduler that never raises into the FastAPI process."""

    def __init__(
        self,
        *,
        service: FeishuDocumentSyncService,
        registrations: tuple[LocalProjectRegistration, ...],
        on_startup: bool = False,
        interval_seconds: int = 0,
        lifecycle: LifecycleBus | None = None,
    ) -> None:
        self._service = service
        self._registrations = registrations
        self._on_startup = on_startup
        self._interval_seconds = max(0, int(interval_seconds))
        self._lifecycle = lifecycle
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_summary: FeishuDocSyncRunSummary | None = None

    @property
    def last_summary(self) -> FeishuDocSyncRunSummary | None:
        with self._lock:
            return self._last_summary

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self._on_startup and self._interval_seconds <= 0:
            return
        if not self._service.available:
            logger.info("feishu doc sync scheduler skipped: API client unavailable")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="feishu-doc-sync-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout: float = 1.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def run_once(self) -> FeishuDocSyncRunSummary:
        summary = sync_registered_projects(
            self._service,
            self._registrations,
            lifecycle=self._lifecycle,
        )
        with self._lock:
            self._last_summary = summary
        return summary

    def _run_loop(self) -> None:
        try:
            if self._on_startup:
                self.run_once()
            while self._interval_seconds > 0 and not self._stop.wait(self._interval_seconds):
                self.run_once()
        except Exception:  # noqa: BLE001 - scheduler must never crash the process
            logger.exception("feishu doc sync scheduler crashed")


def attach_scheduler_to_app(application: Any, scheduler: FeishuDocSyncScheduler) -> None:
    """Store scheduler on app.state for tests and ops inspection."""

    application.state.feishu_doc_sync_scheduler = scheduler
