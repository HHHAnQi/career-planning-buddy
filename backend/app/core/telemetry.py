"""Lightweight correlation context shared by HTTP, Agent, and Provider calls."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelemetryContext:
    trace_id: str | None = None
    run_id: str | None = None
    request_id: str | None = None


_context: ContextVar[TelemetryContext | None] = ContextVar(
    "career_buddy_telemetry_context",
    default=None,
)


def current_telemetry_context() -> TelemetryContext:
    return _context.get() or TelemetryContext()


@contextmanager
def bind_telemetry_context(
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> Iterator[TelemetryContext]:
    """Merge correlation identifiers for the current async execution context."""
    current = current_telemetry_context()
    merged = TelemetryContext(
        trace_id=trace_id if trace_id is not None else current.trace_id,
        run_id=run_id if run_id is not None else current.run_id,
        request_id=request_id if request_id is not None else current.request_id,
    )
    token: Token[TelemetryContext | None] = _context.set(merged)
    try:
        yield merged
    finally:
        _context.reset(token)


class RequestTelemetryMiddleware(BaseHTTPMiddleware):
    """Attach one safe correlation ID to the complete inbound request chain."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "").strip()
        request_id = supplied[:128] if supplied else str(uuid4())
        request.state.request_id = request_id
        started = monotonic()
        try:
            with bind_telemetry_context(trace_id=request_id, request_id=request_id):
                response = await call_next(request)
        except Exception:
            logger.exception(
                "http.request.failed",
                extra={
                    "trace_id": request_id,
                    "request_id": request_id,
                    "http_method": request.method,
                    "http_path": request.url.path,
                    "latency_ms": int((monotonic() - started) * 1000),
                },
            )
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http.request.completed",
            extra={
                "trace_id": request_id,
                "request_id": request_id,
                "http_method": request.method,
                "http_path": request.url.path,
                "http_status": response.status_code,
                "latency_ms": int((monotonic() - started) * 1000),
            },
        )
        return response
