"""JWT issue and validation tests."""

from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import TokenService


def test_token_round_trip_uses_uuid_subject() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret="test-secret-value-with-at-least-32-characters",
    )
    service = TokenService(settings)

    user_id = UUID("3f42b5fa-16b8-45d4-a095-3c2d5dc1a35b")
    token = service.issue(user_id=user_id, role="user")

    assert service.verify(token) == (user_id, "user")
    assert service.expires_in_seconds == 86400


def test_invalid_token_maps_to_auth_error() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret="test-secret-value-with-at-least-32-characters",
    )
    service = TokenService(settings)

    with pytest.raises(AppError) as captured:
        service.verify("not-a-token")

    assert captured.value.code == "AUTH_INVALID_TOKEN"
