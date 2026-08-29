"""Profile use cases, transactions, idempotency, and optimistic locking."""

from enum import Enum
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.models.user_profile import UserProfile
from app.repositories.profiles import ProfileRepository
from app.schemas.profile import (
    ProfilePatchRequest,
    ProfilePreferences,
    ProfilePutRequest,
)


class ProfileService:
    """Own profile creation, replacement, patching, and version rules."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._profiles = ProfileRepository(session)

    async def get_optional(self, user_id: UUID) -> UserProfile | None:
        async with session_transaction(self._session):
            return await self._profiles.get_for_user(user_id)

    async def get(self, user_id: UUID) -> UserProfile:
        profile = await self.get_optional(user_id)
        if profile is None:
            raise AppError(
                code="NOT_FOUND_PROFILE",
                message="profile was not found",
                status_code=HTTPStatus.NOT_FOUND,
            )
        return profile

    async def put(
        self,
        *,
        user_id: UUID,
        payload: ProfilePutRequest,
        idempotency_key: str,
    ) -> UserProfile:
        if not idempotency_key:
            raise ValueError("idempotency key must not be empty")
        values = self._put_values(payload)

        async with session_transaction(self._session):
            existing = await self._profiles.get_for_user(user_id)
            if existing is None:
                return await self._profiles.create(user_id, values)
            if self._matches(existing, values):
                return existing

            updated = await self._profiles.update_with_version(
                user_id=user_id,
                expected_version=existing.version,
                values=values,
            )
            if updated is None:
                raise self._version_conflict(existing.version)
            return updated

    async def patch(
        self,
        *,
        user_id: UUID,
        payload: ProfilePatchRequest,
    ) -> UserProfile:
        values = self._patch_values(payload)
        async with session_transaction(self._session):
            existing = await self._profiles.get_for_user(user_id)
            if existing is None:
                raise AppError(
                    code="NOT_FOUND_PROFILE",
                    message="profile was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if existing.deadline is None and payload.deadline is None:
                raise AppError(
                    code="VALIDATION_PROFILE_DEADLINE_REQUIRED",
                    message="a target date is required before updating the profile",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            if existing.start_date is None and payload.start_date is None:
                raise AppError(
                    code="VALIDATION_PROFILE_START_DATE_REQUIRED",
                    message="a start date is required before updating the profile",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            merged_start = payload.start_date or existing.start_date
            merged_deadline = payload.deadline or existing.deadline
            if merged_start is not None and merged_deadline is not None:
                if merged_start > merged_deadline:
                    raise AppError(
                        code="VALIDATION_PROFILE_PERIOD",
                        message="start_date must be on or before deadline",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                if (merged_deadline - merged_start).days > 55:
                    raise AppError(
                        code="VALIDATION_PROFILE_PERIOD",
                        message="the planning period cannot be more than 8 weeks",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
            updated = await self._profiles.update_with_version(
                user_id=user_id,
                expected_version=payload.version,
                values=values,
            )
            if updated is None:
                raise self._version_conflict(existing.version)
            return updated

    @staticmethod
    def _put_values(payload: ProfilePutRequest) -> dict[str, object]:
        return {
            "goal_type": payload.goal_type.value,
            "stage": payload.stage.value,
            "time_budget_minutes": payload.time_budget_minutes,
            "skill_level": payload.skill_level.value,
            "skill_summary": payload.skill_summary,
            "start_date": payload.start_date,
            "deadline": payload.deadline,
            "preferences": payload.preferences.model_dump(mode="json"),
        }

    @classmethod
    def _patch_values(cls, payload: ProfilePatchRequest) -> dict[str, object]:
        values: dict[str, object] = {}
        for field_name in payload.model_fields_set - {"version"}:
            values[field_name] = cls._serialize_value(getattr(payload, field_name))
        return values

    @staticmethod
    def _serialize_value(value: object) -> object:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, ProfilePreferences):
            return value.model_dump(mode="json")
        return value

    @staticmethod
    def _matches(profile: UserProfile, values: dict[str, object]) -> bool:
        return all(getattr(profile, field_name) == value for field_name, value in values.items())

    @staticmethod
    def _version_conflict(current_version: int) -> AppError:
        return AppError(
            code="STATE_VERSION_CONFLICT",
            message="profile version has changed",
            status_code=HTTPStatus.CONFLICT,
            details={"current_version": current_version},
        )
