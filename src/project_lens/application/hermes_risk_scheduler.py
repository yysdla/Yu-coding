"""Idempotent background risk-review orchestration for Hermes."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
from typing import Callable
from project_lens.application.hermes_risk_assistant import HermesRiskAssistant, HermesRiskBrief
from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_feedback_store import HermesRiskReviewStore, InMemoryHermesRiskReviewStore
from project_lens.domain.models import Evidence, ProjectRef

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class HermesRiskReviewResult:
    project: ProjectRef
    reviewed_risk_ids: tuple[str, ...]
    skipped_risk_ids: tuple[str, ...]
    briefs: tuple[HermesRiskBrief, ...]
    idempotency_keys: tuple[str, ...]

class HermesRiskReviewScheduler:
    """Review open risks without mutating findings or sending duplicates."""
    def __init__(self, *, risk_engine: RiskEngine, assistant: HermesRiskAssistant | None = None, emit: Callable[[HermesRiskBrief], None] | None = None, review_store: HermesRiskReviewStore | None = None) -> None:
        self._risk_engine = risk_engine
        self._assistant = assistant or HermesRiskAssistant()
        self._emit = emit
        self._review_store = review_store or InMemoryHermesRiskReviewStore()

    def run_once(self, project: ProjectRef, evidence: tuple[Evidence, ...], *, now: datetime | None = None) -> HermesRiskReviewResult:
        current = now or datetime.now(timezone.utc)
        reviewed: list[str] = []; skipped: list[str] = []; briefs: list[HermesRiskBrief] = []; keys: list[str] = []
        for finding in self._risk_engine.list_for_project(project):
            if finding.state.value != "open":
                skipped.append(finding.risk_id); continue
            key = f"hermes-risk-review:{project.tenant_id}:{project.project_id}:{finding.risk_id}:{finding.evidence_signature}"
            keys.append(key)
            if not self._review_store.claim_review(key, project):
                skipped.append(finding.risk_id); continue
            brief = self._assistant.build_brief(finding, evidence, now=current)
            reviewed.append(finding.risk_id); briefs.append(brief)
            if self._emit is not None:
                self._emit(brief)
        return HermesRiskReviewResult(project, tuple(reviewed), tuple(skipped), tuple(briefs), tuple(keys))


class HermesRiskBackgroundScheduler:
    """Optional daemon wrapper for project-scoped Hermes risk reviews."""

    def __init__(
        self,
        *,
        projects: tuple[ProjectRef, ...],
        review_scheduler: HermesRiskReviewScheduler,
        evidence_provider: Callable[[ProjectRef], tuple[Evidence, ...]],
        on_startup: bool = False,
        interval_seconds: int = 0,
    ) -> None:
        self._projects = projects
        self._review_scheduler = review_scheduler
        self._evidence_provider = evidence_provider
        self._on_startup = on_startup
        self._interval_seconds = max(0, int(interval_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_results: tuple[HermesRiskReviewResult, ...] = ()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_results(self) -> tuple[HermesRiskReviewResult, ...]:
        with self._lock:
            return self._last_results

    def start(self) -> None:
        if not self._on_startup and self._interval_seconds <= 0:
            return
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="hermes-risk-review", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def run_once(self) -> tuple[HermesRiskReviewResult, ...]:
        results: list[HermesRiskReviewResult] = []
        for project in self._projects:
            try:
                evidence = self._evidence_provider(project)
                results.append(self._review_scheduler.run_once(project, evidence))
            except Exception:  # noqa: BLE001 - one project must not stop the sweep
                logger.exception("Hermes risk review failed for %s/%s", project.tenant_id, project.project_id)
        snapshot = tuple(results)
        with self._lock:
            self._last_results = snapshot
        return snapshot

    def _run_loop(self) -> None:
        try:
            if self._on_startup:
                self.run_once()
            while self._interval_seconds > 0 and not self._stop.wait(self._interval_seconds):
                self.run_once()
        except Exception:  # noqa: BLE001 - scheduler must never crash the process
            logger.exception("Hermes risk background scheduler crashed")


def attach_risk_scheduler_to_app(application: object, scheduler: HermesRiskBackgroundScheduler) -> None:
    application.state.hermes_risk_scheduler = scheduler
