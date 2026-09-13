"""Unified application-error mapping tests."""

from http import HTTPStatus

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import AppError
from app.main import create_app
from app.schemas.errors import ErrorResponse


@pytest.mark.asyncio
async def test_app_error_maps_to_error_response() -> None:
    application = create_app()

    @application.get("/test-error")
    async def raise_test_error() -> None:
        raise AppError(
            code="TEST_CONFLICT",
            message="test conflict",
            status_code=HTTPStatus.CONFLICT,
            details={"field": "value"},
        )

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test-error", headers={"X-Request-ID": "request-123"})

    parsed = ErrorResponse.model_validate(response.json())
    assert response.status_code == HTTPStatus.CONFLICT
    assert parsed.error.code == "TEST_CONFLICT"
    assert parsed.error.request_id == "request-123"
    assert parsed.error.details == {"field": "value"}
