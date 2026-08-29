"""Shared HTTP error response contract."""

from pydantic import Field, JsonValue

from app.schemas.base import StrictModel


class ErrorBody(StrictModel):
    """Details contained in the top-level error envelope."""

    code: str
    message: str
    request_id: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ErrorResponse(StrictModel):
    """Stable API error envelope."""

    error: ErrorBody
