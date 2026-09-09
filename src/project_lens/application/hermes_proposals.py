"""Read-only proposal builders for the Hermes agent.

These functions create drafts only. They never edit files, create PRs, or
change ProjectLens project state.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from project_lens.domain.models import ProjectRef
from project_lens.runtime.patch_plan import FilePatch, PatchPlan


@dataclass(frozen=True)
class HermesProposal:
    id: UUID
    project: ProjectRef
    kind: str
    title: str
    description: str
    evidence_ids: tuple[UUID, ...]
    requires_approval: bool = True


def propose_patch_plan(
    *,
    project: ProjectRef,
    title: str,
    rationale: str,
    patches: tuple[FilePatch, ...] = (),
    test_commands: tuple[str, ...] = (),
    evidence_ids: tuple[UUID, ...] = (),
) -> tuple[HermesProposal, PatchPlan]:
    """Build a bounded patch plan and an approval-required proposal."""
    plan = PatchPlan(
        title=title[:200],
        rationale=rationale[:2_000],
        patches=patches,
        test_commands=test_commands or ("pytest -q",),
    )
    proposal = HermesProposal(
        id=uuid4(),
        project=project,
        kind="patch_plan",
        title=plan.title,
        description=plan.rationale,
        evidence_ids=evidence_ids,
    )
    return proposal, plan


def propose_test_plan(
    *, project: ProjectRef, title: str, commands: tuple[str, ...], evidence_ids: tuple[UUID, ...] = ()
) -> HermesProposal:
    return HermesProposal(
        id=uuid4(), project=project, kind="test_plan", title=title[:200],
        description="\n".join(commands)[:2_000], evidence_ids=evidence_ids,
    )


def propose_risk_escalation(
    *, project: ProjectRef, title: str, description: str, evidence_ids: tuple[UUID, ...] = ()
) -> HermesProposal:
    return HermesProposal(
        id=uuid4(), project=project, kind="risk_escalation", title=title[:200],
        description=description[:2_000], evidence_ids=evidence_ids,
    )


def propose_project_todo(
    *,
    project: ProjectRef,
    title: str,
    description: str,
    owner_ids: tuple[str, ...] = (),
    evidence_ids: tuple[UUID, ...] = (),
) -> HermesProposal:
    owners = tuple(sorted(set(str(item) for item in owner_ids)))
    owner_text = f" Owners: {', '.join(owners)}." if owners else ""
    return HermesProposal(
        id=uuid4(),
        project=project,
        kind="project_todo",
        title=title[:200],
        description=(description[:1_900] + owner_text)[:2_000],
        evidence_ids=evidence_ids,
    )
