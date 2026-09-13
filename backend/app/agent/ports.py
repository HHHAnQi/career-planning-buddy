"""Application ports separating graph orchestration from domain persistence."""

from typing import Protocol
from uuid import UUID

from app.schemas.agent_runs import (
    ClarificationRequest,
    CompanionMessageCandidate,
    EvidenceVisibility,
    NavigationResult,
    PlanCandidate,
    SafeResponse,
)


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
