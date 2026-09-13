"""Strict Stage 6B semantic-knowledge review contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import StrictModel
from app.schemas.enums import GoalType


class ProposedExperienceAtom(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=300)
    source_ids: list[UUID] = Field(min_length=1, max_length=10)
    evidence_excerpt: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)


class DistilledExperienceAtoms(StrictModel):
    candidates: list[ProposedExperienceAtom] = Field(max_length=3)


class ExperienceCandidateView(StrictModel):
    id: UUID
    goal_type: GoalType
    title: str
    content: str
    source_ids: list[UUID]
    evidence_excerpt: str
    confidence: float
    content_hash: str
    status: Literal["pending", "approved", "rejected", "expired"]
    proposed_by_run_id: UUID
    approved_atom_id: UUID | None
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None
