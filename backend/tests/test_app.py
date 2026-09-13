"""FastAPI application and health-check tests."""

from http import HTTPStatus

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api import health as health_api
from app.api.dependencies import get_embedding_provider
from app.core import readiness as core_readiness
from app.core.config import get_settings
from app.main import app, create_app
from app.schemas.health import HealthResponse, ReadinessCheck, ReadinessResponse


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok", "service": "Career Planning Buddy"}


@pytest.mark.asyncio
async def test_liveness_returns_ok() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok", "service": "Career Planning Buddy"}


@pytest.mark.asyncio
async def test_readiness_checks_database_and_migrations(
    db_connection: AsyncConnection,
) -> None:
    result = await core_readiness.build_readiness_response(
        engine=db_connection.engine,
        settings=get_settings(),
    )

    assert result.status == "ready"
    assert result.checks["database"].status == "pass"
    assert result.checks["migrations"].status == "pass"
    assert result.checks["providers"].status == "pass"


@pytest.mark.asyncio
async def test_readiness_detects_schema_revision_mismatch(
    db_connection: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        core_readiness,
        "expected_database_revision",
        lambda: "not-current",
    )

    result = await core_readiness.build_readiness_response(
        engine=db_connection.engine,
        settings=get_settings(),
    )

    assert result.status == "not_ready"
    assert result.checks["migrations"].status == "fail"


@pytest.mark.asyncio
async def test_readiness_rejects_schema_revision_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def not_ready_response(**_kwargs: object) -> ReadinessResponse:
        return ReadinessResponse(
            status="not_ready",
            service="Career Planning Buddy",
            checks={
                "migrations": ReadinessCheck(
                    status="fail",
                    detail="revision mismatch",
                )
            },
        )

    monkeypatch.setattr(health_api, "build_readiness_response", not_ready_response)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["migrations"]["status"] == "fail"


@pytest.mark.asyncio
async def test_health_response_matches_schema() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    parsed = HealthResponse.model_validate(response.json())
    assert parsed.status == "ok"


@pytest.mark.asyncio
async def test_health_allows_configured_local_frontend_origin() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"


@pytest.mark.asyncio
async def test_profile_cors_preflight_allows_stage_one_headers() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/v1/profile",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": ("authorization,content-type,idempotency-key"),
            },
        )

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "PUT" in response.headers["Access-Control-Allow-Methods"]
    allowed_headers = response.headers["Access-Control-Allow-Headers"].lower()
    assert "authorization" in allowed_headers
    assert "idempotency-key" in allowed_headers


@pytest.mark.asyncio
async def test_sse_cors_preflight_allows_authorization_and_resume_header() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/v1/agent-runs/00000000-0000-0000-0000-000000000000/events",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,last-event-id",
            },
        )

    assert response.status_code == HTTPStatus.OK
    allowed_headers = response.headers["Access-Control-Allow-Headers"].lower()
    assert "authorization" in allowed_headers
    assert "last-event-id" in allowed_headers


def test_health_response_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        HealthResponse.model_validate(
            {
                "status": "ok",
                "service": "Career Planning Buddy",
                "unexpected": True,
            }
        )


def test_application_initializes() -> None:
    application = create_app()

    assert application.title == "Career Planning Buddy"
    assert str(application.url_path_for("health_check")) == "/health"


def test_application_reuses_one_embedding_provider() -> None:
    application = create_app()
    request = Request({"type": "http", "app": application})

    assert get_embedding_provider(request) is application.state.runtime_providers.embedding
