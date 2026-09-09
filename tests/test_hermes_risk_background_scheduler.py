from project_lens.application.hermes_risk_scheduler import HermesRiskBackgroundScheduler, HermesRiskReviewScheduler
from project_lens.application.risk_engine import RiskEngine
from project_lens.application.risk_store import InMemoryRiskStore
from project_lens.domain.models import ProjectRef


def test_background_scheduler_is_disabled_until_explicitly_started() -> None:
    scheduler = HermesRiskBackgroundScheduler(
        projects=(ProjectRef(tenant_id="demo", project_id="payment"),),
        review_scheduler=HermesRiskReviewScheduler(risk_engine=RiskEngine(InMemoryRiskStore())),
        evidence_provider=lambda _project: (),
    )
    scheduler.start()
    assert scheduler.running is False
    assert scheduler.last_results == ()


def test_background_scheduler_run_once_records_project_results() -> None:
    project = ProjectRef(tenant_id="demo", project_id="payment")
    scheduler = HermesRiskBackgroundScheduler(
        projects=(project,),
        review_scheduler=HermesRiskReviewScheduler(risk_engine=RiskEngine(InMemoryRiskStore())),
        evidence_provider=lambda _project: (),
    )
    results = scheduler.run_once()
    assert len(results) == 1
    assert scheduler.last_results[0].project == project
