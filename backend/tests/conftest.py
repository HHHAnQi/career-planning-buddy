"""Shared Stage 1 PostgreSQL and API fixtures."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent.eval_executor import EvalRunnerExecutor
from app.agent.executor import AgentRunExecutor
from app.api.dependencies import (
    get_agent_run_executor,
    get_eval_runner_executor,
    get_goal_understanding_provider,
)
from app.core.config import get_settings
from app.core.database import get_db_session
from app.main import create_app
from app.providers.goal_understanding import RuleGoalUnderstandingProvider
from evals.v2.dataset_loader import DatasetBundle

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("EVAL_PROVIDER_MODE", "fixture")
# Tests use the SAME isolated database as CI (career_buddy_test). Sharing
# the development database lets a concurrently running backend container
# claim test-created pending runs through the shared lease queue and
# execute them with the container's real providers — breaking trial
# fixture determinism (observed locally: mock-configured runs executed
# with the container's live LLM).
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://career_buddy:career_buddy_local@localhost:5432/career_buddy_test",
)
os.environ.setdefault("JWT_SECRET", "pytest-only-secret-with-at-least-32-characters")


def _prepare_test_database() -> None:
    """Create and migrate the isolated test database when missing.

    CI provisions ``career_buddy_test`` via the service container and has
    already run migrations; CREATE DATABASE then no-ops and ``upgrade
    head`` is a fast no-op. Locally this bootstraps the same database on
    first use.
    """

    url = os.environ["DATABASE_URL"]
    database = url.rsplit("/", 1)[-1]
    base, admin_database = url.rsplit("/", 1)
    if database != "career_buddy_test":
        return

    async def create_database() -> None:
        admin_dsn = f"{base}/postgres".replace("postgresql+asyncpg://", "postgresql://")
        connection = await asyncpg.connect(admin_dsn)
        try:
            await connection.execute(f'CREATE DATABASE "{database}"')
        except asyncpg.DuplicateDatabaseError:
            pass
        finally:
            await connection.close()

    asyncio.run(create_database())

    # Imports above (app.main …) may have already cached Settings bound to
    # the .env development URL; reset so alembic's env.py resolves the
    # isolated test database.
    get_settings.cache_clear()

    from alembic.config import Config

    from alembic import command

    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option(
        "script_location", str(backend_root / "alembic")
    )
    os.environ["DATABASE_URL"] = url
    command.upgrade(alembic_config, "head")


_prepare_test_database()
get_settings.cache_clear()


class StubAgentRunExecutor(AgentRunExecutor):
    """API-test scheduler that records submission without leaving the test transaction."""

    def __init__(self) -> None:
        self.submitted: list[UUID] = []
        self.cancelled: list[UUID] = []

    def submit(self, run_id: UUID) -> None:
        self.submitted.append(run_id)

    async def request_cancel(self, run_id: UUID) -> None:
        self.cancelled.append(run_id)


class StubEvalRunnerExecutor(EvalRunnerExecutor):
    """API-test eval executor that records submission without spawning a task."""

    def __init__(self) -> None:
        self.submitted: list[tuple[UUID, bool]] = []
        self.cancelled: list[UUID] = []

    def submit(self, experiment_id: UUID, dataset: DatasetBundle, *, grade: bool = True) -> None:
        _ = dataset  # not needed for the recorded submit signature
        self.submitted.append((experiment_id, grade))

    async def request_cancel(self, experiment_id: UUID) -> None:
        self.cancelled.append(experiment_id)


@pytest_asyncio.fixture
async def db_connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session_factory = async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def api_application(db_session: AsyncSession) -> AsyncIterator[FastAPI]:
    application = create_app()
    executor = StubAgentRunExecutor()
    eval_executor = StubEvalRunnerExecutor()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db_session] = override_db_session
    application.dependency_overrides[get_agent_run_executor] = lambda: executor
    application.dependency_overrides[get_eval_runner_executor] = lambda: eval_executor
    application.dependency_overrides[get_goal_understanding_provider] = (
        RuleGoalUnderstandingProvider
    )
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def api_client(api_application: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=api_application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
