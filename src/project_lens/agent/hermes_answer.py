"""Turn a Hermes tool-loop result into a citation-checked ProjectAnswer."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from project_lens.agent.draft import AnswerDraft
from project_lens.agent.read_tools import InvestigationLedger
from project_lens.agent.verifier import verify_answer_draft
from project_lens.domain.models import Evidence, EvidenceType, ProjectAnswer, ProjectRef, SourceRef


def project_answer_from_hermes(
    *, project: ProjectRef, draft: AnswerDraft, tool_calls: tuple[object, ...]
) -> ProjectAnswer:
    """Verify Hermes citations using bounded citation metadata from tool results."""
    ledger = InvestigationLedger()
    for call in tool_calls:
        envelope = getattr(call, "envelope", {})
        for ref in (envelope.get("evidence_refs") or envelope.get("citations") or []):
            if not isinstance(ref, dict):
                continue
            evidence = _citation_to_evidence(project, ref)
            if evidence is not None:
                ledger.add_evidence(evidence)
    ledger.tool_names.extend(
        str(getattr(call, "name", "")) for call in tool_calls if getattr(call, "name", "")
    )
    answer = verify_answer_draft(draft, project=project, ledger=ledger)
    return answer.model_copy(update={"skill": "project_investigation", "policy_used": "hermes-citation-ledger-v1"})


def _citation_to_evidence(project: ProjectRef, ref: dict[str, object]) -> Evidence | None:
    try:
        evidence_id = UUID(str(ref.get("id") or ""))
    except ValueError:
        return None
    source_uri = str(ref.get("source_uri") or "hermes:unknown")[:500]
    system, _, source_id = source_uri.partition(":")
    if not source_id:
        system, source_id = "hermes", source_uri
    summary = str(ref.get("summary") or "citation metadata").strip()[:300] or "citation metadata"
    return Evidence(
        id=evidence_id,
        type=_kind_to_type(str(ref.get("kind") or "document")),
        project=project,
        source=SourceRef(system=system[:50] or "hermes", source_id=source_id[:500]),
        content=summary,
        observed_at=datetime.now(timezone.utc),
        access_scope=f"project:{project.project_id}:read",
        content_hash=sha256(summary.encode("utf-8")).hexdigest(),
        metadata={"citation_only": True, "source_uri": source_uri},
    )


def _kind_to_type(kind: str) -> EvidenceType:
    return {
        "doc": EvidenceType.DOCUMENT,
        "document": EvidenceType.DOCUMENT,
        "code": EvidenceType.CODE,
        "commit": EvidenceType.COMMIT,
        "pull_request": EvidenceType.PULL_REQUEST,
        "pr": EvidenceType.PULL_REQUEST,
        "task": EvidenceType.TASK,
        "incident": EvidenceType.INCIDENT,
        "log": EvidenceType.LOG,
        "metric": EvidenceType.METRIC,
    }.get(kind.casefold(), EvidenceType.DOCUMENT)
