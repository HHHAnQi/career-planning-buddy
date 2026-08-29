"""User profile persistence operations."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_profile import UserProfile


class ProfileRepository:
    """Profile queries that always require an authenticated user scope."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: UUID) -> UserProfile | None:
        result = await self._session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create(self, user_id: UUID, values: dict[str, object]) -> UserProfile:
        profile = UserProfile(user_id=user_id, **values)
        self._session.add(profile)
        await self._session.flush()
        await self._session.refresh(profile)
        return profile

    async def update_with_version(
        self,
        *,
        user_id: UUID,
        expected_version: int,
        values: dict[str, object],
    ) -> UserProfile | None:
        statement = (
            update(UserProfile)
            .where(
                UserProfile.user_id == user_id,
                UserProfile.version == expected_version,
            )
            .values(
                **values,
                version=UserProfile.version + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(UserProfile)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()
