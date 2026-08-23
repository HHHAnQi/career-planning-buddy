"""Serializable Stage 4 Tool contracts and handler registration metadata."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.base import StrictModel
from app.schemas.enums import GoalType, RunIntent


class ModelToolSpec(StrictModel):
    name: str
    description: str
    input_json_schema: dict[str, object]
    contract_version: str


class ToolContext(StrictModel):
    run_id: UUID
    user_id: UUID
    goal_type: GoalType
    intent: RunIntent = RunIntent.CREATE_PLAN
    requires_fresh_information: bool = False
    remaining_deadline_ms: int = Field(ge=0)
    replay_fixture_run_id: UUID | None = None
    fixture_only: bool = False


class ToolResult(StrictModel):
    tool_name: str
    data: dict[str, object]
    evidence: list["EvidenceItem"] = Field(default_factory=list)
    truncated: bool = False
    provider: str | None = None


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[BaseModel]]


class EvidenceItem(StrictModel):
    kind: Literal[
        "memory",
        "experience_atom",
        "search_source",
        "interview_turn",
        "resume_claim",
        "job_requirement",
        "rag_document_chunk",
    ]
    id: UUID
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=2000)
    reliability: float = Field(ge=0, le=1)


class MemoryLookupInput(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5)


class MemoryLookupItem(StrictModel):
    memory_id: UUID
    content: str
    memory_type: str
    relevance: float = Field(ge=0, le=1)
    updated_at: datetime


class MemoryLookupOutput(StrictModel):
    items: list[MemoryLookupItem]
    evidence: list[EvidenceItem] = Field(default_factory=list)


class RagRetrieveInput(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    goal_type: GoalType
    limit: int = Field(default=5, ge=1, le=5)


class RagEvidenceItem(StrictModel):
    atom_id: UUID
    title: str
    content: str
    evidence: str
    reliability: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)


class RagRetrieveOutput(StrictModel):
    items: list[RagEvidenceItem]
    evidence: list[EvidenceItem] = Field(default_factory=list)


class DocumentSearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5)


class DocumentSearchItem(StrictModel):
    chunk_id: UUID
    doc_kind: Literal["resume", "job_target"]
    source_id: UUID
    title: str
    content: str
    rerank_score: float = Field(ge=0, le=1)
    vector_rank: int | None = None
    lexical_rank: int | None = None


class DocumentSearchOutput(StrictModel):
    items: list[DocumentSearchItem]
    # False means no chunk cleared the answerability gate: the caller must
    # not answer from these results.
    sufficient: bool = True
    evidence: list[EvidenceItem] = Field(default_factory=list)


class WebSearchInput(StrictModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5)
    freshness_days: int | None = Field(default=None, ge=1, le=365)


class WebSearchItem(StrictModel):
    source_id: UUID
    url: str
    title: str | None
    snippet: str
    source_type: str
    reliability: float = Field(ge=0, le=1)
    retrieved_at: datetime


class WebSearchOutput(StrictModel):
    items: list[WebSearchItem]
    evidence: list[EvidenceItem] = Field(default_factory=list)


class InterviewEvidenceQuery(StrictModel):
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{16}$")
    claim_text: str = Field(min_length=1, max_length=1000)


class InterviewEvidenceRetrieveInput(StrictModel):
    interview_session_id: UUID
    claims: list[InterviewEvidenceQuery] = Field(min_length=1, max_length=30)
    limit_per_claim: int = Field(default=3, ge=1, le=3)


class InterviewEvidenceRetrieveItem(StrictModel):
    claim_id: str = Field(pattern=r"^claim_[0-9a-f]{16}$")
    turn_id: UUID
    question: str = Field(max_length=300)
    answer: str = Field(max_length=500)
    relevance: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)
    explicit_conflict: bool = False


class InterviewEvidenceRetrieveOutput(StrictModel):
    items: list[InterviewEvidenceRetrieveItem]
    evidence: list[EvidenceItem] = Field(default_factory=list)


class ResumeGapAnalyzeInput(StrictModel):
    resume_version_id: UUID
    job_target_id: UUID
    claim_ids: list[str] = Field(min_length=1, max_length=80)


class ResumeGapItem(StrictModel):
    claim_id: str
    requirement_ids: list[str] = Field(max_length=5)
    coverage_score: float = Field(ge=0, le=1)
    gap: Literal["covered", "partial", "uncovered"]


class ResumeGapAnalyzeOutput(StrictModel):
    items: list[ResumeGapItem]
    evidence: list[EvidenceItem] = Field(default_factory=list)


class ToolExecutionResult(StrictModel):
    success: bool
    result: ToolResult
    tool_call_id: UUID | None = None
    error_code: str | None = None
    reused: bool = False


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ModelToolSpec
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    timeout_seconds: float = 8
    max_result_chars: int = 6000
    stage: int = 4
    provider: str | None = None
