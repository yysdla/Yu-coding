from uuid import uuid4

from project_lens.application.memory_service import (
    consolidate_episodes,
    propose_memory_from_episode,
    submit_consolidated_episodes,
)
from project_lens.context.memory_store import InMemoryMemoryStore
from project_lens.runtime.memory_approval_gateway import MemoryApprovalGateway
from project_lens.domain.memory import Episode
from project_lens.domain.models import ProjectRef


def _episode(*, summary: str, evidence_ids=()) -> Episode:
    return Episode(
        project=ProjectRef(tenant_id="demo", project_id="payment"),
        run_id=uuid4(),
        title="支付回调调查",
        summary=summary,
        started_at=__import__("datetime").datetime(2026, 9, 1),
        ended_at=__import__("datetime").datetime(2026, 9, 1),
        status="completed",
        evidence_ids=tuple(evidence_ids),
    )


def test_episode_consolidation_requires_evidence_and_creates_pending_proposal() -> None:
    evidence_id = uuid4()
    proposal = propose_memory_from_episode(
        _episode(summary="支付回调必须通过 Kafka", evidence_ids=(evidence_id,)),
        proposed_by="hermes",
    )

    assert proposal is not None
    assert proposal.status == "pending"
    assert proposal.evidence_ids == (evidence_id,)
    assert proposal.project.project_id == "payment"


def test_episode_consolidation_does_not_promote_placeholder_or_unsupported_summary() -> None:
    assert propose_memory_from_episode(_episode(summary="未生成回答", evidence_ids=(uuid4(),)), proposed_by="hermes") is None
    assert propose_memory_from_episode(_episode(summary="支付回调必须通过 Kafka"), proposed_by="hermes") is None


def test_batch_consolidation_deduplicates_and_keeps_proposal_only_boundary() -> None:
    evidence_a, evidence_b = uuid4(), uuid4()
    first = _episode(summary="支付回调必须通过 Kafka", evidence_ids=(evidence_a,))
    duplicate = _episode(summary="支付回调必须通过 Kafka。", evidence_ids=(evidence_b,))
    missing = _episode(summary="前端按钮规范")

    report = consolidate_episodes((first, duplicate, missing), proposed_by="hermes")

    assert report["proposal_count"] == 1
    assert report["skipped"]["duplicate"] == 1
    assert report["skipped"]["missing_evidence"] == 1
    proposal = report["proposals"][0]
    assert proposal.status == "pending"
    assert proposal.evidence_ids == (evidence_a,)


def test_batch_submission_is_idempotent_and_uses_approval_gateway() -> None:
    evidence_id = uuid4()
    episode = _episode(summary="支付回调必须通过 Kafka", evidence_ids=(evidence_id,))
    gateway = MemoryApprovalGateway(InMemoryMemoryStore())

    first = submit_consolidated_episodes((episode,), approval_gateway=gateway, proposed_by="hermes")
    second = submit_consolidated_episodes((episode,), approval_gateway=gateway, proposed_by="hermes")

    assert first["submitted_count"] == 1
    assert second["submitted_count"] == 1
    assert first["proposals"][0].id == second["proposals"][0].id
    assert gateway.audit_summary()["pending_creates"] == 2

    proposal_id = first["proposals"][0].id
    gateway.decide_memory_proposal(proposal_id, approved=True, decided_by="manager")
    third = submit_consolidated_episodes((episode,), approval_gateway=gateway, proposed_by="hermes")
    assert third["proposals"][0].status == "approved"
