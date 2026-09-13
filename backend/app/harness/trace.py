"""Persisted Agent node step sequencing and completion."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentStep


class TraceRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start_step(self, run_id: UUID, node_name: str, attempt: int) -> AgentStep:
        result = await self._session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(next_step_sequence=AgentRun.next_step_sequence + 1)
            .returning(AgentRun.next_step_sequence)
        )
        sequence = result.scalar_one() - 1
        step = AgentStep(
            run_id=run_id,
            sequence=sequence,
            node_name=node_name,
            attempt=attempt,
            status="running",
            trace_data={},
        )
        self._session.add(step)
        await self._session.flush()
        await self._session.refresh(step)
        return step

    async def complete_step(
        self,
        step: AgentStep,
        *,
        status: str,
        latency_ms: int,
        trace_data: dict[str, object],
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_cny: Decimal = Decimal("0"),
        model_id: str | None = None,
        prompt_version: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        step.status = status
        step.latency_ms = latency_ms
        step.trace_data = trace_data
        step.tokens_in = tokens_in
        step.tokens_out = tokens_out
        step.cost_cny = cost_cny
        step.model_id = model_id
        step.prompt_version = prompt_version
        step.error_code = error_code
        step.error_message = error_message
        step.finished_at = datetime.now(UTC)
        await self._session.flush()
