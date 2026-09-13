"""User persistence operations."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """User queries scoped by stable identity fields."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_guest_device_hash(self, device_hash: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.guest_device_hash == device_hash)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )
        return result.scalar_one_or_none()

    async def create_guest_without_device(self) -> User:
        user = User()
        self._session.add(user)
        await self._session.flush()
        return user

    async def create_or_get_guest_by_hash(self, device_hash: str) -> tuple[User, bool]:
        statement = (
            insert(User)
            .values(guest_device_hash=device_hash)
            .on_conflict_do_nothing(
                index_elements=[User.guest_device_hash],
                index_where=User.guest_device_hash.is_not(None),
            )
            .returning(User)
        )
        result = await self._session.execute(statement)
        created_user = result.scalar_one_or_none()
        if created_user is not None:
            return created_user, True

        existing_user = await self.get_by_guest_device_hash(device_hash)
        if existing_user is None:
            raise RuntimeError("guest device conflict did not resolve to a user")
        return existing_user, False

    async def create_email_user(
        self,
        *,
        email: str,
        password_hash: str,
        display_name: str | None,
    ) -> User:
        user = User(
            auth_type="email",
            email=email.lower(),
            password_hash=password_hash,
            display_name=display_name,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def delete(self, user: User) -> None:
        await self._session.delete(user)
        await self._session.flush()
