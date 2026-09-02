"""Stage 4 memory and consent API contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import StrictModel

MemoryType = Literal["profile_fact", "stable_preference", "execution_pattern"]
MemoryStatus = Literal["active", "closed"]
CandidateStatus = Literal["pending", "confirmed", "rejected", "expired"]


class MemoryResponse(StrictModel):
    memory_id: UUID
    memory_type: MemoryType
    summary: str
    content: dict[str, object]
    sensitivity: Literal["normal", "sensitive"]
    status: MemoryStatus
    source_run_id: UUID | None
    version: int = Field(ge=1)
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(StrictModel):
    items: list[MemoryResponse]
    next_cursor: UUID | None = None


class MemoryPatchRequest(StrictModel):
    status: MemoryStatus
    version: int = Field(ge=1)


class MemoryCandidateResponse(StrictModel):
    candidate_id: UUID
    memory_type: MemoryType
    summary: str
    content: dict[str, object]
    sensitivity: Literal["sensitive", "highly_sensitive"]
    status: CandidateStatus
    proposed_by_run_id: UUID | None
    activated_memory_id: UUID | None
    expires_at: datetime
    created_at: datetime
    decided_at: datetime | None


class MemoryCandidateListResponse(StrictModel):
    items: list[MemoryCandidateResponse]
    next_cursor: UUID | None = None


class MemoryCandidateDecisionResponse(StrictModel):
    candidate: MemoryCandidateResponse
    memory: MemoryResponse | None = None
