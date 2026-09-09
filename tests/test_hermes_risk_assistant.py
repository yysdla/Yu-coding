from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4
from project_lens.application.hermes_risk_assistant import HermesRiskAssistant
from project_lens.domain.models import Evidence, EvidenceType, ProjectRef, SourceRef
from project_lens.domain.risk import RiskFinding, RiskSeverity, RiskState, RiskType, evidence_signature, stable_risk_id

def test_risk_brief_is_evidence_grounded_and_non_mutating() -> None:
    now = datetime(2026, 9, 2, tzinfo=timezone.utc); project = ProjectRef(tenant_id="demo", project_id="payment"); evidence_id = uuid4(); body = "CI failed"
    evidence = Evidence(id=evidence_id, type=EvidenceType.PULL_REQUEST, project=project, source=SourceRef(system="ci", source_id="run-1"), content=body, observed_at=now, access_scope="project:read", content_hash=sha256(body.encode()).hexdigest(), metadata={})
    finding = RiskFinding(risk_id=stable_risk_id(project, RiskType.PR_REVIEW_OR_CI, "run-1"), project=project, risk_type=RiskType.PR_REVIEW_OR_CI, severity=RiskSeverity.HIGH, title="CI failure", summary="Main branch CI is failing.", primary_ref="run-1", evidence_ids=(evidence_id,), evidence_signature=evidence_signature(((evidence_id, evidence.content_hash),)), detected_at=now, last_seen_at=now, state=RiskState.OPEN, owner_ids=("u1",))
    brief = HermesRiskAssistant().build_brief(finding, (evidence,), now=now, create_escalation_proposal=True)
    assert brief.state is RiskState.OPEN; assert brief.evidence_ids == (str(evidence_id),); assert brief.proposal is not None; assert any("不自动关闭" in action for action in brief.recommended_actions)
