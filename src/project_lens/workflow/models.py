"""Contracts passed between workflow agents."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from project_lens.domain.models import Claim, ClaimType, EvidenceGrade, ProjectRef
from project_lens.workflow.skills import ProjectSkill


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AnswerAudience(StrEnum):
    BUSINESS = "business"
    TECHNICAL = "technical"
    BOTH = "both"


class CandidateClaim(FrozenModel):
    text: str = Field(min_length=1)
    type: ClaimType
    grade: EvidenceGrade
    evidence_ids: tuple[UUID, ...] = ()
    support_terms: tuple[str, ...] = ()
    audience: AnswerAudience = AnswerAudience.BOTH


class CandidateAction(FrozenModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    evidence_ids: tuple[UUID, ...] = ()
    support_terms: tuple[str, ...] = ()


class AnalysisResult(FrozenModel):
    problem_kind: str
    skill: ProjectSkill = ProjectSkill.PROJECT_KNOWLEDGE
    candidates: tuple[CandidateClaim, ...] = ()
    actions: tuple[CandidateAction, ...] = ()
    unknowns: tuple[str, ...] = ()


class ProjectRegistration(FrozenModel):
    project: ProjectRef
    access_scope: str = Field(min_length=1, max_length=200)


class VerificationIssue(FrozenModel):
    item: str
    reason: str


class VerificationResult(FrozenModel):
    claims: tuple[Claim, ...] = ()
    actions: tuple[CandidateAction, ...] = ()
    issues: tuple[VerificationIssue, ...] = ()


class ResolvedProject(FrozenModel):
    project: ProjectRef
    access_scope: str = Field(min_length=1, max_length=200)
    resolution: str
