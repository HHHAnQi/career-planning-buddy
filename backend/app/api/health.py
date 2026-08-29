"""Infrastructure liveness and readiness endpoints."""

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.core.database import engine
from app.core.readiness import build_readiness_response
from app.schemas.health import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Backward-compatible shallow liveness probe."""
    return HealthResponse(status="ok", service=get_settings().app_name)


@router.get("/health/live", response_model=HealthResponse)
async def liveness_check() -> HealthResponse:
    """Report process liveness without touching external dependencies."""
    return HealthResponse(status="ok", service=get_settings().app_name)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "Database, migration, or Provider configuration is not ready",
        }
    },
)
async def readiness_check(response: Response) -> ReadinessResponse:
    """Report whether this build can safely accept application traffic."""
    result = await build_readiness_response(engine=engine, settings=get_settings())
    if result.status != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
