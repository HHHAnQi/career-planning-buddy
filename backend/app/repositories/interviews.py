"""User-scoped InterviewSession and InterviewTurn persistence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interview import InterviewSession, InterviewTurn


class InterviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_session(
        self, session_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> InterviewSession | None:
        statement = select(InterviewSession).where(
            InterviewSession.id == session_id, InterviewSession.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_key(self, user_id: UUID, key: str) -> InterviewSession | None:
        result = await self._session.execute(
            select(InterviewSession).where(
                InterviewSession.user_id == user_id, InterviewSession.idempotency_key == key
            )
        )
        return result.scalar_one_or_none()

    async def list_sessions(self, user_id: UUID) -> list[InterviewSession]:
        rows = await self._session.scalars(
            select(InterviewSession)
            .where(InterviewSession.user_id == user_id)
            .order_by(InterviewSession.created_at.desc())
        )
        return list(rows)

    async def list_completed_reports(self, user_id: UUID) -> list[InterviewSession]:
        rows = await self._session.scalars(
            select(InterviewSession)
            .where(
                InterviewSession.user_id == user_id,
                InterviewSession.report_status == "ready",
                InterviewSession.report_json.is_not(None),
            )
            .order_by(InterviewSession.completed_at.desc(), InterviewSession.created_at.desc())
        )
        return list(rows)

    async def create_session(self, row: InterviewSession) -> InterviewSession:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def delete_session(self, row: InterviewSession) -> None:
        await self._session.delete(row)
        await self._session.flush()

    async def get_turn(
        self, turn_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> InterviewTurn | None:
        statement = select(InterviewTurn).where(
            InterviewTurn.id == turn_id, InterviewTurn.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_turns(self, session_id: UUID, user_id: UUID) -> list[InterviewTurn]:
        rows = await self._session.scalars(
            select(InterviewTurn)
            .where(InterviewTurn.session_id == session_id, InterviewTurn.user_id == user_id)
            .order_by(InterviewTurn.ordinal)
        )
        return list(rows)

    async def create_turn(self, row: InterviewTurn) -> InterviewTurn:
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def count_submitted_turns(self, session_id: UUID, user_id: UUID) -> int:
        from sqlalchemy import func

        value = await self._session.scalar(
            select(func.count(InterviewTurn.id)).where(
                InterviewTurn.session_id == session_id,
                InterviewTurn.user_id == user_id,
                InterviewTurn.answer_status == "submitted",
            )
        )
        return int(value or 0)
