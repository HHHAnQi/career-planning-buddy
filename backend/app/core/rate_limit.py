"""HTTP boundary guard: request metrics plus per-identity rate limiting.

Every request increments ``http_requests_total`` and observes latency in
``http_request_duration_seconds`` regardless of the rate-limit setting.

Rate limiting is a fixed-window counter keyed by client IP and a hash of
the Authorization header, so distinct authenticated users get separate
buckets even when they share an IP, while unauthenticated traffic falls
back to one bucket per IP. ``rate_limit_per_minute=0`` (the default)
disables limiting — Compose deployments opt in via ``.env``.

Exempt paths (health, metrics, docs) are never limited and never rejected.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.metrics import (
    HTTP_IN_FLIGHT,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    RATE_LIMIT_REJECTIONS,
    make_labels,
    normalize_path,
)

_WINDOW_SECONDS = 60.0
_EXEMPT_PATHS = frozenset(
    {"/health", "/health/live", "/health/ready", "/metrics", "/docs", "/openapi.json"}
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Record HTTP metrics and reject requests beyond the per-minute budget."""

    def __init__(self, app, *, requests_per_minute: int = 0) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._requests_per_minute = requests_per_minute
        self._hits: dict[tuple[int, str], int] = defaultdict(int)
        self._last_sweep = 0.0

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.monotonic()
        path = normalize_path(request.url.path)
        method = request.method
        HTTP_IN_FLIGHT.inc()
        response: Response
        try:
            if (
                self._requests_per_minute > 0
                and request.method != "OPTIONS"
                and request.url.path not in _EXEMPT_PATHS
                and self._over_limit(request)
            ):
                RATE_LIMIT_REJECTIONS.inc(make_labels(path=path))
                response = _too_many_requests()
            else:
                response = await call_next(request)
        except Exception:
            # The exception escapes before any response exists; status 500
            # is an approximation so server-side failures stay observable.
            HTTP_REQUESTS.inc(make_labels(method=method, path=path, status="500"))
            HTTP_REQUEST_DURATION.observe(
                make_labels(method=method, path=path), time.monotonic() - started
            )
            raise
        finally:
            HTTP_IN_FLIGHT.dec()
        HTTP_REQUESTS.inc(
            make_labels(method=method, path=path, status=str(response.status_code))
        )
        HTTP_REQUEST_DURATION.observe(
            make_labels(method=method, path=path), time.monotonic() - started
        )
        return response

    def _over_limit(self, request: Request) -> bool:
        window = int(time.monotonic() // _WINDOW_SECONDS)
        key = (window, self._identity(request))
        self._hits[key] += 1
        self._sweep_expired(window)
        return self._hits[key] > self._requests_per_minute

    def _identity(self, request: Request) -> str:
        client_ip = request.client.host if request.client is not None else "unknown"
        authorization = request.headers.get("Authorization", "")
        if not authorization:
            return f"ip:{client_ip}"
        digest = hashlib.sha256(authorization.encode("utf-8")).hexdigest()[:16]
        return f"auth:{digest}"

    def _sweep_expired(self, current_window: int) -> None:
        """Drop closed windows so the in-memory map stays bounded."""

        if current_window == self._last_sweep:
            return
        self._last_sweep = current_window
        expired = [key for key in self._hits if key[0] < current_window]
        for key in expired:
            del self._hits[key]


def _too_many_requests() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Retry after the current window."},
        headers={"Retry-After": str(int(_WINDOW_SECONDS))},
    )
