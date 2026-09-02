"""Atomic persisted event sequencing for SSE."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentEvent, AgentRun

TERMINAL_EVENTS = {"run.completed", "run.degraded", "run.failed", "run.cancelled"}


class EventRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        run_id: UUID,
        event_type: str,
        payload: dict[str, object],
        *,
        allow_terminal_run: bool = False,
    ) -> AgentEvent:
        if event_type == "heartbeat":
            raise ValueError("heartbeat events are never persisted")
        if not allow_terminal_run:
            status = await self._session.scalar(
                select(AgentRun.status).where(AgentRun.id == run_id)
            )
            if status not in {"pending", "running"}:
                raise RuntimeError("cannot append an event after a terminal Run")
        result = await self._session.execute(
            update(AgentRun)
            .where(AgentRun.id == run_id)
            .values(next_event_sequence=AgentRun.next_event_sequence + 1)
            .returning(AgentRun.next_event_sequence)
        )
        next_sequence = result.scalar_one()
        sequence = next_sequence - 1
        event_payload = {**payload, "run_id": str(run_id), "sequence": sequence}
        event = AgentEvent(
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            payload_json=event_payload,
        )
        self._session.add(event)
        await self._session.flush()
        return event
