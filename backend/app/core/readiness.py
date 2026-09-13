"""Dependency readiness checks that never invoke billable external APIs."""

import asyncio
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import BACKEND_ROOT, Settings
from app.core.provider_status import build_provider_configuration_status
from app.schemas.health import ReadinessCheck, ReadinessResponse


@lru_cache
def expected_database_revision(backend_root: Path = BACKEND_ROOT) -> str:
    """Resolve the single Alembic head shipped with this application build."""
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("application requires exactly one Alembic head")
    return heads[0]


async def build_readiness_response(
    *,
    engine: AsyncEngine,
    settings: Settings,
) -> ReadinessResponse:
    """Check database connectivity, schema revision, and Provider configuration."""
    checks: dict[str, ReadinessCheck] = {}
    current_revision: str | None = None
    try:
        async with asyncio.timeout(2):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                revision = await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                current_revision = str(revision) if revision is not None else None
        checks["database"] = ReadinessCheck(status="pass", detail="reachable")
    except (SQLAlchemyError, TimeoutError):
        checks["database"] = ReadinessCheck(status="fail", detail="unavailable")

    try:
        expected_revision = expected_database_revision()
    except (OSError, RuntimeError):
        expected_revision = None
    if current_revision is None or expected_revision is None:
        checks["migrations"] = ReadinessCheck(
            status="fail",
            detail="revision unavailable",
        )
    elif current_revision != expected_revision:
        checks["migrations"] = ReadinessCheck(
            status="fail",
            detail=f"database={current_revision}; application={expected_revision}",
        )
    else:
        checks["migrations"] = ReadinessCheck(
            status="pass",
            detail=current_revision,
        )

    provider_status = build_provider_configuration_status(settings)
    provider_detail = (
        "; ".join(provider_status.warnings)
        if provider_status.warnings
        else "configured"
    )
    checks["providers"] = ReadinessCheck(
        status="pass" if provider_status.ready else "fail",
        detail=provider_detail,
    )
    ready = all(check.status == "pass" for check in checks.values())
    return ReadinessResponse(
        status="ready" if ready else "not_ready",
        service=settings.app_name,
        checks=checks,
    )
