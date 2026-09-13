"""Guest authentication and profile API contract tests."""

from datetime import timedelta
from http import HTTPStatus

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import product_today
from app.models.user import User
from app.models.user_profile import UserProfile


async def guest_login(
    client: AsyncClient,
    device_id: str | None = None,
) -> tuple[str, str, int]:
    payload = {} if device_id is None else {"device_id": device_id}
    response = await client.post("/api/v1/auth/guest", json=payload)
    body = response.json()
    return body["access_token"], body["user"]["id"], response.status_code


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def profile_body(time_budget_minutes: int = 120) -> dict[str, object]:
    return {
        "goal_type": "agent_app",
        "stage": "preparing",
        "time_budget_minutes": time_budget_minutes,
        "skill_level": "intermediate",
        "skill_summary": "FastAPI and RAG",
        "start_date": product_today().isoformat(),
        "deadline": (product_today() + timedelta(days=34)).isoformat(),
        "preferences": {
            "target_companies": ["Example Company"],
            "preferred_time_slot": "evening",
            "weekly_available_days": [1, 2, 3, 4, 5],
        },
    }


@pytest.mark.asyncio
async def test_guest_login_creates_then_reuses_device(api_client: AsyncClient) -> None:
    device_id = "browser-device-id-reuse-0001"

    first_token, first_user_id, first_status = await guest_login(api_client, device_id)
    second_token, second_user_id, second_status = await guest_login(api_client, device_id)

    assert first_status == HTTPStatus.CREATED
    assert second_status == HTTPStatus.OK
    assert first_user_id == second_user_id
    assert first_token
    assert second_token


@pytest.mark.asyncio
async def test_email_register_login_reuses_same_account_and_profile(
    api_client: AsyncClient,
) -> None:
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "AnQi@example.com",
            "password": "correct-password",
            "display_name": "AnQi",
        },
    )
    token = registered.json()["access_token"]
    user_id = registered.json()["user"]["id"]

    await api_client.put(
        "/api/v1/profile",
        json=profile_body(90),
        headers={**bearer(token), "Idempotency-Key": "email-profile-create"},
    )
    logged_in = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "anqi@example.com", "password": "correct-password"},
    )
    restored = await api_client.get(
        "/api/v1/me",
        headers=bearer(logged_in.json()["access_token"]),
    )

    assert registered.status_code == HTTPStatus.CREATED
    assert registered.json()["user"]["email"] == "anqi@example.com"
    assert logged_in.status_code == HTTPStatus.OK
    assert logged_in.json()["user"]["id"] == user_id
    assert restored.status_code == HTTPStatus.OK
    assert restored.json()["user"]["email"] == "anqi@example.com"
    assert restored.json()["profile"]["time_budget_minutes"] == 90


@pytest.mark.asyncio
async def test_email_auth_rejects_duplicate_and_wrong_password(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    created = await api_client.post(
        "/api/v1/auth/register",
        json={"email": "owner@example.com", "password": "correct-password"},
    )
    duplicate = await api_client.post(
        "/api/v1/auth/register",
        json={"email": "OWNER@example.com", "password": "another-password"},
    )
    wrong_password = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password"},
    )
    user = await db_session.scalar(select(User).where(User.email == "owner@example.com"))

    assert created.status_code == HTTPStatus.CREATED
    assert duplicate.status_code == HTTPStatus.CONFLICT
    assert duplicate.json()["error"]["code"] == "AUTH_EMAIL_EXISTS"
    assert wrong_password.status_code == HTTPStatus.UNAUTHORIZED
    assert wrong_password.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert user is not None
    assert user.password_hash is not None
    assert "correct-password" not in user.password_hash


@pytest.mark.asyncio
async def test_me_reports_incomplete_then_complete_profile(api_client: AsyncClient) -> None:
    token, _, _ = await guest_login(api_client)

    before = await api_client.get("/api/v1/me", headers=bearer(token))
    created = await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "profile-api-create-1"},
    )
    after = await api_client.get("/api/v1/me", headers=bearer(token))

    assert before.status_code == HTTPStatus.OK
    assert before.json()["profile_complete"] is False
    assert before.json()["profile"] is None
    assert created.status_code == HTTPStatus.OK
    assert created.json()["version"] == 1
    assert after.json()["profile_complete"] is True
    assert after.json()["planning_window_valid"] is True
    assert after.json()["profile"]["goal_type"] == "agent_app"


@pytest.mark.asyncio
async def test_expired_planning_window_does_not_make_product_profile_incomplete(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token, user_id, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "expired-window-profile"},
    )
    profile = await db_session.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    assert profile is not None
    profile.start_date = product_today() - timedelta(days=30)
    profile.deadline = product_today() - timedelta(days=1)
    await db_session.flush()

    response = await api_client.get("/api/v1/me", headers=bearer(token))

    assert response.status_code == HTTPStatus.OK
    assert response.json()["profile_complete"] is True
    assert response.json()["planning_window_valid"] is False


@pytest.mark.asyncio
async def test_profile_get_missing_and_invalid_token_use_error_contract(
    api_client: AsyncClient,
) -> None:
    token, _, _ = await guest_login(api_client)

    missing = await api_client.get("/api/v1/profile", headers=bearer(token))
    invalid = await api_client.get(
        "/api/v1/profile",
        headers=bearer("invalid-token"),
    )

    assert missing.status_code == HTTPStatus.NOT_FOUND
    assert missing.json()["error"]["code"] == "NOT_FOUND_PROFILE"
    assert invalid.status_code == HTTPStatus.UNAUTHORIZED
    assert invalid.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


@pytest.mark.asyncio
async def test_profile_patch_increments_version_and_rejects_conflict(
    api_client: AsyncClient,
) -> None:
    token, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "profile-api-create-2"},
    )

    updated = await api_client.patch(
        "/api/v1/profile",
        json={"version": 1, "time_budget_minutes": 90},
        headers=bearer(token),
    )
    conflict = await api_client.patch(
        "/api/v1/profile",
        json={"version": 1, "time_budget_minutes": 60},
        headers=bearer(token),
    )

    assert updated.status_code == HTTPStatus.OK
    assert updated.json()["version"] == 2
    assert updated.json()["time_budget_minutes"] == 90
    assert conflict.status_code == HTTPStatus.CONFLICT
    assert conflict.json()["error"]["code"] == "STATE_VERSION_CONFLICT"
    assert conflict.json()["error"]["details"]["current_version"] == 2


@pytest.mark.asyncio
async def test_tokens_only_access_their_own_profiles(api_client: AsyncClient) -> None:
    token_a, user_a, _ = await guest_login(api_client)
    token_b, user_b, _ = await guest_login(api_client)
    assert user_a != user_b

    await api_client.put(
        "/api/v1/profile",
        json=profile_body(60),
        headers={**bearer(token_a), "Idempotency-Key": "profile-user-a"},
    )
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(180),
        headers={**bearer(token_b), "Idempotency-Key": "profile-user-b"},
    )

    profile_a = await api_client.get("/api/v1/profile", headers=bearer(token_a))
    profile_b = await api_client.get("/api/v1/profile", headers=bearer(token_b))

    assert profile_a.json()["time_budget_minutes"] == 60
    assert profile_b.json()["time_budget_minutes"] == 180


@pytest.mark.asyncio
async def test_profile_request_rejects_user_id_and_missing_idempotency_key(
    api_client: AsyncClient,
) -> None:
    token, user_id, _ = await guest_login(api_client)
    with_user_id = await api_client.put(
        "/api/v1/profile",
        json={**profile_body(), "user_id": user_id},
        headers={**bearer(token), "Idempotency-Key": "profile-invalid-user"},
    )
    without_key = await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers=bearer(token),
    )

    assert with_user_id.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert with_user_id.json()["error"]["code"] == "VALIDATION_PROFILE_INVALID"
    assert without_key.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert without_key.json()["error"]["code"] == "VALIDATION_PROFILE_INVALID"


@pytest.mark.asyncio
async def test_delete_me_removes_current_guest_and_invalidates_its_token(
    api_client: AsyncClient,
) -> None:
    token, _, _ = await guest_login(api_client)
    await api_client.put(
        "/api/v1/profile",
        json=profile_body(),
        headers={**bearer(token), "Idempotency-Key": "profile-before-account-delete"},
    )

    deleted = await api_client.delete("/api/v1/me", headers=bearer(token))
    after = await api_client.get("/api/v1/me", headers=bearer(token))

    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert after.status_code == HTTPStatus.UNAUTHORIZED
