"""Application ports separating graph orchestration from domain persistence."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.agent.context_selection import MemorySelectionResult
from app.schemas.agent_runs import (
    ClarificationRequest,
    CompanionMessageCandidate,
    EvidenceVisibility,
    HistoryParcel,
    NavigationResult,
    PlanCandidate,
    ProfileContext,
    SafeResponse,
)


@dataclass(frozen=True, slots=True)
class MemoryQueryFacts:
    """Minimal persisted facts needed to construct the memory query."""

    blockers: list[str]
    adjustment_request: str | None


class PlanningContextPort(Protocol):
    """Read-only persistence boundary for context loader graph nodes."""

    async def load_profile(self, *, user_id: UUID) -> ProfileContext | None: ...

    async def load_memory_query_facts(
        self,
        *,
        user_id: UUID,
        source_review_id: UUID | None,
    ) -> MemoryQueryFacts: ...

    async def load_history(
        self,
        *,
        user_id: UUID,
        source_plan_id: UUID | None,
        source_review_id: UUID | None,
        source_interview_report_session_id: UUID | None,
        request_message: str,
    ) -> HistoryParcel: ...

    async def select_memories(
        self,
        *,
        user_id: UUID,
        user_message: str,
        goal_type: str,
        blockers: list[str],
        adjustment_request: str | None,
        semantic_enabled: bool,
        retrieval_limit: int,
        max_items: int,
        max_chars: int,
        min_similarity: float,
        half_life_days: int,
        exclude_categories: set[str] | None,
        precomputed_vector: list[float] | None,
    ) -> MemorySelectionResult: ...


class PlanningResultPort(Protocol):
    """Persistence boundary used by the execution context to publish outcomes."""

    async def finalize_plan(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
        candidate: PlanCandidate,
        evidence_visibility: EvidenceVisibility,
        companion: CompanionMessageCandidate,
        persist_step_id: UUID,
        fallback_reason: str | None,
        simulate_failure: bool = False,
    ) -> None: ...

    async def finalize_degraded(
        self,
        *,
        run_id: UUID,
        result_kind: str,
        result: ClarificationRequest | SafeResponse | NavigationResult,
        fallback_reason: str,
    ) -> None: ...
