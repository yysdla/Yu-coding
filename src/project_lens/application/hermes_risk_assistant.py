"""Evidence-grounded Hermes assistance for explaining project risks."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from project_lens.application.hermes_proposals import HermesProposal, propose_risk_escalation
from project_lens.domain.models import Evidence, ProjectRef
from project_lens.domain.risk import RiskFinding, RiskSeverity, RiskState, ensure_utc

@dataclass(frozen=True)
class HermesRiskBrief:
    risk_id: str
    project: ProjectRef
    severity: RiskSeverity
    state: RiskState
    explanation: str
    impact: str
    recommended_actions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    review_at: datetime
    proposal: HermesProposal | None = None

class HermesRiskAssistant:
    def build_brief(self, finding: RiskFinding, evidence: tuple[Evidence, ...], *, now: datetime | None = None, create_escalation_proposal: bool = False) -> HermesRiskBrief:
        current = ensure_utc(now or datetime.now(timezone.utc))
        linked_ids = {item.id for item in evidence}
        count = sum(item_id in linked_ids for item_id in finding.evidence_ids)
        explanation = f"{finding.summary} Evidence confirms {count} linked source(s)."
        impact = _impact_for(finding.severity)
        actions = _actions_for(finding)
        proposal = propose_risk_escalation(project=finding.project, title=f"Escalate risk: {finding.title}", description=f"{impact} Recommended: {'; '.join(actions)}", evidence_ids=finding.evidence_ids) if create_escalation_proposal else None
        return HermesRiskBrief(finding.risk_id, finding.project, finding.severity, finding.state, explanation[:2000], impact, actions, tuple(str(item) for item in finding.evidence_ids), current + timedelta(days=1), proposal)

def _impact_for(severity: RiskSeverity) -> str:
    if severity == RiskSeverity.HIGH:
        return "可能影响当前项目交付或线上稳定性，应优先通知负责人并安排复查。"
    if severity == RiskSeverity.MEDIUM:
        return "可能造成范围、进度或质量风险，建议在下一次项目同步前处理。"
    return "当前影响有限，但应保留跟踪，避免风险持续累积。"

def _actions_for(finding: RiskFinding) -> tuple[str, ...]:
    actions = ["确认负责人和处理截止时间", "补充或复核关联证据"]
    if finding.state == RiskState.OPEN:
        actions.insert(0, "由负责人确认风险并选择处理路径")
    if finding.owner_ids:
        actions.append(f"通知负责人：{', '.join(finding.owner_ids)}")
    actions.append("安排 24 小时后复查；Hermes 不自动关闭风险")
    return tuple(actions)

