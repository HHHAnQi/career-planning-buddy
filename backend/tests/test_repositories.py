"""PostgreSQL repository integration tests."""

from hashlib import sha256

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.repositories.profiles import ProfileRepository
from app.repositories.users import UserRepository


@pytest.mark.asyncio
async def test_guest_device_hash_is_reused_without_storing_raw_value(
    db_session: AsyncSession,
) -> None:
    raw_device_id = "browser-device-id-0001"
    device_hash = sha256(raw_device_id.encode()).hexdigest()
    users = UserRepository(db_session)

    async with session_transaction(db_session):
        first, first_created = await users.create_or_get_guest_by_hash(device_hash)
    async with session_transaction(db_session):
        second, second_created = await users.create_or_get_guest_by_hash(device_hash)

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert first.guest_device_hash == device_hash
    assert raw_device_id not in first.guest_device_hash


@pytest.mark.asyncio
async def test_profile_repository_scopes_reads_and_optimistic_updates(
    db_session: AsyncSession,
) -> None:
    users = UserRepository(db_session)
    profiles = ProfileRepository(db_session)
    async with session_transaction(db_session):
        user_a = await users.create_guest_without_device()
        user_b = await users.create_guest_without_device()
        created = await profiles.create(
            user_a.id,
            {
                "goal_type": "agent_app",
                "stage": "preparing",
                "time_budget_minutes": 120,
                "skill_level": "intermediate",
                "preferences": {},
            },
        )
        initial_version = created.version

    async with session_transaction(db_session):
        scoped_a = await profiles.get_for_user(user_a.id)
        scoped_b = await profiles.get_for_user(user_b.id)
        updated = await profiles.update_with_version(
            user_id=user_a.id,
            expected_version=initial_version,
            values={"time_budget_minutes": 90},
        )
        stale = await profiles.update_with_version(
            user_id=user_a.id,
            expected_version=initial_version,
            values={"time_budget_minutes": 60},
        )

    assert scoped_a is not None
    assert scoped_b is None
    assert updated is not None
    assert updated.version == 2
    assert stale is None
