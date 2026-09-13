"""Health-check response contract."""

from typing import Literal

from app.schemas.base import StrictModel


class HealthResponse(StrictModel):
    """Stable response returned by the infrastructure health probe."""

    status: Literal["ok"]
    service: str


class ReadinessCheck(StrictModel):
    status: Literal["pass", "fail"]
    detail: str


class ReadinessResponse(StrictModel):
    status: Literal["ready", "not_ready"]
    service: str
    checks: dict[str, ReadinessCheck]
