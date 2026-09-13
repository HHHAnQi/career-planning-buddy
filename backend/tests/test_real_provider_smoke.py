"""Regression coverage for the rollback-only real-Provider smoke workflow."""

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings
from app.providers.llm import MockPlanningProvider
from scripts.real_provider_smoke import _run_in_transaction


@pytest.mark.asyncio
async def test_smoke_workflow_completes_with_provider_protocol(
    db_connection: AsyncConnection,
) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=(
            "postgresql+asyncpg://career_buddy:career_buddy_local@127.0.0.1:5432/career_buddy"
        ),
        jwt_secret="test-secret-with-at-least-32-characters",
        llm_provider="mock",
    )

    result = await _run_in_transaction(
        db_connection,
        settings,
        MockPlanningProvider(),
    )

    create_plan = result["create_plan"]
    review_replan = result["review_replan"]
    assert isinstance(create_plan, dict)
    assert isinstance(review_replan, dict)
    assert create_plan["status"] == "completed"
    assert review_replan["status"] == "completed"
