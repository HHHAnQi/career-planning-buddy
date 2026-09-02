"""User-scoped Goal Brief persistence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal_brief import GoalBrief


class GoalBriefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(
        self, brief_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> GoalBrief | None:
        statement = select(GoalBrief).where(GoalBrief.id == brief_id, GoalBrief.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_idempotency(self, user_id: UUID, key: str) -> GoalBrief | None:
        return (
            await self._session.execute(
                select(GoalBrief).where(
                    GoalBrief.user_id == user_id, GoalBrief.idempotency_key == key
                )
            )
        ).scalar_one_or_none()

    async def get_active_for_user(self, user_id: UUID) -> GoalBrief | None:
        return (
            await self._session.execute(
                select(GoalBrief).where(
                    GoalBrief.user_id == user_id,
                    GoalBrief.status.in_(("clarification_required", "awaiting_confirmation")),
                )
            )
        ).scalar_one_or_none()

    async def create(self, brief: GoalBrief) -> GoalBrief:
        self._session.add(brief)
        await self._session.flush()
        await self._session.refresh(brief)
        return brief
