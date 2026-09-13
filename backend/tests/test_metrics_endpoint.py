"""Tests for the ``/metrics`` scrape endpoint.

Pins:
* Serves Prometheus text format (0.0.4) under ``text/plain``.
* Reflects real traffic: a preceding request shows up in the exposition.
* Excluded from the OpenAPI contract so the snapshot stays stable.
* Never rate limited even with a tiny budget (covered by exemption list).
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_metrics_serves_prometheus_text(api_client: AsyncClient) -> None:
    # Generate at least one tracked request before scraping.
    await api_client.get("/health")
    response = await api_client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_requests_total counter" in response.text
    assert 'path="/health"' in response.text


@pytest.mark.asyncio
async def test_metrics_endpoint_not_in_openapi_contract(api_client: AsyncClient) -> None:
    response = await api_client.get("/openapi.json")
    assert response.status_code == 200
    assert "/metrics" not in response.json()["paths"]
