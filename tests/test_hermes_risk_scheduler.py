from datetime import datetime, timezone
from project_lens.application.hermes_risk_scheduler import HermesRiskReviewScheduler
from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_store import InMemoryRiskStore
from project_lens.application.risk_feedback_store import InMemoryHermesRiskReviewStore
from project_lens.domain.models import ProjectRef

def test_review_scheduler_is_idempotent_for_same_evidence_snapshot() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    emitted = []
    scheduler = HermesRiskReviewScheduler(risk_engine=RiskEngine(InMemoryRiskStore()), emit=emitted.append)
    first = scheduler.run_once(project, (), now=datetime(2026, 9, 2, tzinfo=timezone.utc))
    second = scheduler.run_once(project, (), now=datetime(2026, 9, 2, tzinfo=timezone.utc))
    assert first.reviewed_risk_ids == ()
    assert second.reviewed_risk_ids == ()
    assert emitted == []


def test_review_scheduler_can_use_persistent_claim_store() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    store = InMemoryHermesRiskReviewStore()
    first = HermesRiskReviewScheduler(risk_engine=RiskEngine(InMemoryRiskStore()), review_store=store)
    second = HermesRiskReviewScheduler(risk_engine=RiskEngine(InMemoryRiskStore()), review_store=store)
    assert first.run_once(project, ()).reviewed_risk_ids == ()
    assert second.run_once(project, ()).reviewed_risk_ids == ()
