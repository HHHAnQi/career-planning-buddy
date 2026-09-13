"""User-scoped Agent Run persistence operations."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentEvent, AgentRun

TERMINAL_STATUSES = ("completed", "degraded", "failed", "cancelled")
ACTIVE_STATUSES = ("pending", "running")


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(
        self, run_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> AgentRun | None:
        statement = select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id(self, run_id: UUID, *, for_update: bool = False) -> AgentRun | None:
        statement: Select[tuple[AgentRun]] = select(AgentRun).where(AgentRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_idempotency(self, user_id: UUID, key: str) -> AgentRun | None:
        result = await self._session.execute(
            select(AgentRun).where(
                AgentRun.user_id == user_id,
                AgentRun.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_goal_brief(self, brief_id: UUID, user_id: UUID) -> AgentRun | None:
        result = await self._session.execute(
            select(AgentRun).where(
                AgentRun.goal_brief_id == brief_id,
                AgentRun.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_interview(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        run_kind: str,
        turn_id: UUID | None = None,
    ) -> AgentRun | None:
        statement = select(AgentRun).where(
            AgentRun.interview_session_id == session_id,
            AgentRun.user_id == user_id,
            AgentRun.run_kind == run_kind,
        )
        if turn_id is not None:
            statement = statement.where(AgentRun.interview_turn_id == turn_id)
        result = await self._session.execute(statement.order_by(AgentRun.created_at.desc()))
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: UUID) -> AgentRun | None:
        result = await self._session.execute(
            select(AgentRun).where(
                AgentRun.user_id == user_id,
                AgentRun.status.in_(ACTIVE_STATUSES),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_for_interview(
        self, session_id: UUID, user_id: UUID
    ) -> AgentRun | None:
        result = await self._session.execute(
            select(AgentRun)
            .where(
                AgentRun.interview_session_id == session_id,
                AgentRun.user_id == user_id,
                AgentRun.status.in_(ACTIVE_STATUSES),
            )
            .order_by(AgentRun.created_at.desc())
        )
        return result.scalar_one_or_none()

    async def create(self, run: AgentRun) -> AgentRun:
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def request_cancel(self, run_id: UUID, user_id: UUID) -> AgentRun | None:
        result = await self._session.execute(
            update(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.status.in_(ACTIVE_STATUSES),
            )
            .values(cancel_requested_at=datetime.now(UTC))
            .returning(AgentRun)
        )
        return result.scalar_one_or_none()

    async def list_events_after(
        self, run_id: UUID, user_id: UUID, sequence: int
    ) -> list[AgentEvent]:
        result = await self._session.execute(
            select(AgentEvent)
            .join(AgentRun, AgentRun.id == AgentEvent.run_id)
            .where(
                AgentEvent.run_id == run_id,
                AgentRun.user_id == user_id,
                AgentEvent.sequence > sequence,
            )
            .order_by(AgentEvent.sequence)
        )
        return list(result.scalars())

    async def mark_expired_active_runs_interrupted(self) -> list[UUID]:
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(AgentRun.id).where(
                AgentRun.status.in_(ACTIVE_STATUSES),
                AgentRun.deadline_at <= now,
            )
        )
        return list(result.scalars())
