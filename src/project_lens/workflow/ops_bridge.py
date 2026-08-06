"""Collect ProjectOps window findings for incident-style questions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from project_lens.context.models import AccessContext, EvidenceBundle, RetrievalHit
from project_lens.domain.models import Evidence, ProjectRef
from project_lens.domain.ops import OpsFinding, OpsQuery
from project_lens.workflow.skills import ProjectSkill


class _OpsQueryable(Protocol):
    def query_ops(self, query: OpsQuery, access: AccessContext) -> OpsFinding: ...


def maybe_collect_ops_finding(
    ops_source: _OpsQueryable,
    *,
    project: ProjectRef,
    access: AccessContext,
    skill: ProjectSkill,
    question: str,
    bundle: EvidenceBundle,
) -> tuple[OpsFinding | None, EvidenceBundle]:
    if skill != ProjectSkill.INCIDENT_DIAGNOSIS:
        return None, bundle
    start, end = _ops_window(bundle)
    finding = ops_source.query_ops(
        OpsQuery(
            project=project,
            start=start,
            end=end,
            environment=project.environment,
            service=project.service,
            text_filter=_text_filter(question),
            limit=20,
        ),
        access,
    )
    if not finding.evidence:
        return finding, bundle
    return finding, _enrich_bundle_with_ops_evidence(bundle, finding.evidence)


def _ops_window(bundle: EvidenceBundle) -> tuple[datetime, datetime]:
    anchors = [
        item.observed_at
        for item in bundle.evidence
        if item.type.value in {"incident", "log", "metric", "commit"}
    ]
    if anchors:
        center = max(anchors)
    else:
        center = datetime.now(timezone.utc)
    return center - timedelta(hours=6), center + timedelta(hours=6)


def _text_filter(question: str) -> str | None:
    lowered = question.casefold()
    for marker in ("coupon", "checkout", "create_order", "http 500", "attributeerror"):
        if marker in lowered:
            return marker
    return None


def _enrich_bundle_with_ops_evidence(
    bundle: EvidenceBundle,
    evidence: tuple[Evidence, ...],
) -> EvidenceBundle:
    known = {item.id for item in bundle.evidence}
    extras = [item for item in evidence if item.id not in known]
    if not extras:
        return bundle
    extra_hits = tuple(
        RetrievalHit(
            evidence=item,
            score=0.7,
            channels=("ops_window",),
            channel_ranks={"ops_window": index + 1},
        )
        for index, item in enumerate(extras)
    )
    trace = dict(bundle.retrieval_trace)
    channel_counts = dict(trace.get("channel_counts") or {})
    channel_counts["ops_window"] = len(extra_hits)
    trace["channel_counts"] = channel_counts
    trace["ops_evidence_count"] = len(extra_hits)
    return EvidenceBundle(
        query=bundle.query,
        hits=bundle.hits + extra_hits,
        retrieval_trace=trace,
        warnings=bundle.warnings,
    )
