"""Stage 1 service transaction and state tests."""

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import TokenService
from app.core.time import product_today
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePatchRequest, ProfilePutRequest
from app.services.auth import AuthService
from app.services.profiles import ProfileService


def profile_payload() -> ProfilePutRequest:
    return ProfilePutRequest(
        goal_type=GoalType.AGENT_APP,
        stage=CareerStage.PREPARING,
        time_budget_minutes=120,
        skill_level=SkillLevel.INTERMEDIATE,
        skill_summary="FastAPI and RAG",
        start_date=product_today(),
        deadline=product_today() + timedelta(days=27),
    )


@pytest.mark.asyncio
async def test_profile_put_is_semantically_idempotent(db_session: AsyncSession) -> None:
    auth = AuthService(db_session, TokenService(get_settings()))
    user = (await auth.login_guest(None)).user
    service = ProfileService(db_session)

    first = await service.put(
        user_id=user.id,
        payload=profile_payload(),
        idempotency_key="profile-create-1",
    )
    repeated = await service.put(
        user_id=user.id,
        payload=profile_payload(),
        idempotency_key="profile-create-1",
    )

    assert first.version == 1
    assert repeated.version == 1


@pytest.mark.asyncio
async def test_profile_patch_rejects_stale_version(db_session: AsyncSession) -> None:
    auth = AuthService(db_session, TokenService(get_settings()))
    user = (await auth.login_guest(None)).user
    service = ProfileService(db_session)
    await service.put(
        user_id=user.id,
        payload=profile_payload(),
        idempotency_key="profile-create-2",
    )
    updated = await service.patch(
        user_id=user.id,
        payload=ProfilePatchRequest(version=1, time_budget_minutes=90),
    )
    updated_version = updated.version

    with pytest.raises(AppError) as captured:
        await service.patch(
            user_id=user.id,
            payload=ProfilePatchRequest(version=1, time_budget_minutes=60),
        )

    assert updated_version == 2
    assert captured.value.code == "STATE_VERSION_CONFLICT"
