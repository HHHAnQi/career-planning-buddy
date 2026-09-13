"""Alembic environment wired to the application's async database configuration."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app import models as application_models
from app.core.config import get_settings
from app.core.database import Base

config = context.config
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
registered_models = (
    application_models.User,
    application_models.UserProfile,
    application_models.AgentRun,
    application_models.AgentStep,
    application_models.ToolCall,
    application_models.AgentEvent,
    application_models.AgentRuntimeBundle,
    application_models.AgentCheckpoint,
    application_models.ReplayComparison,
    application_models.ResumeVersion,
    application_models.JobTarget,
    application_models.InterviewSession,
    application_models.InterviewTurn,
    application_models.ResumeAssessment,
    application_models.ResumeRewriteDecision,
    application_models.Plan,
    application_models.Task,
    application_models.CompanionMessage,
    application_models.Review,
    application_models.Memory,
    application_models.MemoryCandidate,
    application_models.SearchSource,
    application_models.ExperienceAtom,
    application_models.ExperienceAtomCandidate,
    application_models.EvalExperiment,
    application_models.EvalTrial,
    application_models.EvalScore,
)


def run_migrations_offline() -> None:
    """Run migrations without creating an Engine."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure and execute migrations on a synchronous connection proxy."""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an AsyncEngine and run migrations through its sync bridge."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
