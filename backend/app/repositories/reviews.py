"""User-scoped Review persistence operations."""

from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import CompanionMessage
from app.models.review import Review


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, values: dict[str, object]) -> Review:
        review = Review(**values)
        self._session.add(review)
        await self._session.flush()
        await self._session.refresh(review)
        return review

    async def delete(self, review: Review) -> None:
        await self._session.delete(review)
        await self._session.flush()

    async def get_for_user(
        self,
        review_id: UUID,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> Review | None:
        statement = select(Review).where(Review.id == review_id, Review.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_idempotency(self, user_id: UUID, key: str) -> Review | None:
        result = await self._session.execute(
            select(Review).where(
                Review.user_id == user_id,
                Review.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_plan_date(
        self,
        user_id: UUID,
        plan_id: UUID,
        review_date: date,
    ) -> Review | None:
        result = await self._session.execute(
            select(Review).where(
                Review.user_id == user_id,
                Review.plan_id == plan_id,
                Review.review_date == review_date,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        plan_id: UUID | None,
        date_from: date | None,
        date_to: date | None,
        cursor: UUID | None,
        limit: int,
    ) -> list[Review]:
        statement = select(Review).where(Review.user_id == user_id)
        if plan_id is not None:
            statement = statement.where(Review.plan_id == plan_id)
        if date_from is not None:
            statement = statement.where(Review.review_date >= date_from)
        if date_to is not None:
            statement = statement.where(Review.review_date <= date_to)
        if cursor is not None:
            cursor_review = await self.get_for_user(cursor, user_id)
            if cursor_review is None:
                return []
            statement = statement.where(
                or_(
                    Review.created_at < cursor_review.created_at,
                    (
                        (Review.created_at == cursor_review.created_at)
                        & (Review.id < cursor_review.id)
                    ),
                )
            )
        result = await self._session.execute(
            statement.order_by(Review.created_at.desc(), Review.id.desc()).limit(limit)
        )
        return list(result.scalars())

    async def recent_for_plan(
        self,
        user_id: UUID,
        plan_id: UUID,
        *,
        before_date: date | None = None,
        limit: int = 7,
    ) -> list[Review]:
        statement = select(Review).where(
            Review.user_id == user_id,
            Review.plan_id == plan_id,
        )
        if before_date is not None:
            statement = statement.where(Review.review_date < before_date)
        result = await self._session.execute(
            statement.order_by(Review.review_date.desc(), Review.created_at.desc()).limit(limit)
        )
        return list(result.scalars())

    async def companion_for_review(
        self,
        review_id: UUID,
        user_id: UUID,
    ) -> CompanionMessage | None:
        result = await self._session.execute(
            select(CompanionMessage)
            .where(
                CompanionMessage.review_id == review_id,
                CompanionMessage.user_id == user_id,
            )
            .order_by(CompanionMessage.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
