"""Persistence operations for auditable Task adjustment proposals."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import TaskAdjustmentProposal


class TaskAdjustmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, values: dict[str, object]) -> TaskAdjustmentProposal:
        proposal = TaskAdjustmentProposal(**values)
        self._session.add(proposal)
        await self._session.flush()
        await self._session.refresh(proposal)
        return proposal

    async def get_for_user(
        self, adjustment_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> TaskAdjustmentProposal | None:
        statement = select(TaskAdjustmentProposal).where(
            TaskAdjustmentProposal.id == adjustment_id,
            TaskAdjustmentProposal.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_idempotency(
        self, user_id: UUID, idempotency_key: str
    ) -> TaskAdjustmentProposal | None:
        result = await self._session.execute(
            select(TaskAdjustmentProposal).where(
                TaskAdjustmentProposal.user_id == user_id,
                TaskAdjustmentProposal.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_prefix(
        self, user_id: UUID, prefix: str
    ) -> list[TaskAdjustmentProposal]:
        result = await self._session.execute(
            select(TaskAdjustmentProposal).where(
                TaskAdjustmentProposal.user_id == user_id,
                TaskAdjustmentProposal.idempotency_key.like(f"{prefix}%"),
            )
        )
        return list(result.scalars())
