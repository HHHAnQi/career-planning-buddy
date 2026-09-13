"""Prometheus scrape endpoint for the in-process metrics registry.

Infrastructure endpoint: intentionally excluded from the OpenAPI contract
(``include_in_schema=False``) and from rate limiting. The deployment is
single-node; if the backend is ever exposed publicly, put /metrics behind
an authenticating reverse proxy before enabling scrapes.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.metrics import render_metrics

router = APIRouter(tags=["metrics"])

_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(content=render_metrics(), media_type=_MEDIA_TYPE)
