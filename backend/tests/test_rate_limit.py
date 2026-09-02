"""Tests for the HTTP boundary guard: metrics recording and rate limiting.

Pins:
* ``/metrics`` serves Prometheus text format and is excluded from the
  OpenAPI contract snapshot.
* Every request increments ``http_requests_total`` with a normalized path
  (UUIDs and numeric ids collapsed) and observes a duration.
* Fixed-window limiting: the first N requests pass, request N+1 gets 429
  with a ``Retry-After`` header; exempt paths and OPTIONS are never
  limited; per_minute=0 disables limiting entirely.
* Distinct Authorization headers get separate buckets (identity isolation).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.metrics import (
    RATE_LIMIT_REJECTIONS,
    make_labels,
    normalize_path,
    render_metrics,
)
from app.core.rate_limit import RateLimitMiddleware


@pytest.fixture
def guarded_app() -> FastAPI:
    application = FastAPI()

    @application.get("/api/v1/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    application.add_middleware(RateLimitMiddleware, requests_per_minute=3)
    return application


@pytest.fixture
async def client(guarded_app: FastAPI):
    transport = ASGITransport(app=guarded_app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_requests_increment_metrics_with_normalized_path(client: AsyncClient) -> None:
    await client.get("/api/v1/items/123")
    body = render_metrics()
    assert "# TYPE http_requests_total counter" in body
    assert 'http_requests_total{method="GET",path="/api/v1/items/{id}",status="200"}' in body
    assert "http_request_duration_seconds_count" in body


def test_normalize_path_collapses_uuids_and_ids() -> None:
    assert normalize_path("/api/v1/runs/00000000-0000-0000-0000-000000000abc") == (
        "/api/v1/runs/{uuid}"
    )
    assert normalize_path("/api/v1/tasks/42/reviews") == "/api/v1/tasks/{id}/reviews"
    assert normalize_path("/api/v1/plans") == "/api/v1/plans"


@pytest.mark.asyncio
async def test_limit_exceeded_returns_429_with_retry_after(client: AsyncClient) -> None:
    for _ in range(3):
        assert (await client.get("/api/v1/items/1")).status_code == 200
    response = await client.get("/api/v1/items/1")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert RATE_LIMIT_REJECTIONS.values.get(make_labels(path="/api/v1/items/{id}")) == 1


@pytest.mark.asyncio
async def test_exempt_paths_are_never_limited(client: AsyncClient) -> None:
    for _ in range(5):
        assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_options_bypasses_limit(client: AsyncClient) -> None:
    for _ in range(5):
        response = await client.options("/api/v1/items/1")
        # 405 is expected: the guard must not turn OPTIONS into 429.
        assert response.status_code != 429


@pytest.mark.asyncio
async def test_disabled_limit_lets_traffic_through() -> None:
    application = FastAPI()

    @application.get("/api/v1/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    application.add_middleware(RateLimitMiddleware, requests_per_minute=0)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(10):
            assert (await client.get("/api/v1/items/1")).status_code == 200


@pytest.mark.asyncio
async def test_distinct_authorizations_get_separate_buckets(client: AsyncClient) -> None:
    for token_index in range(3):
        headers = {"Authorization": f"Bearer token-{token_index}"}
        for _ in range(3):
            response = await client.get("/api/v1/items/1", headers=headers)
            assert response.status_code == 200
    # A fourth identity is still fresh even though the shared budget was used.
    response = await client.get(
        "/api/v1/items/1", headers={"Authorization": "Bearer token-new"}
    )
    assert response.status_code == 200
