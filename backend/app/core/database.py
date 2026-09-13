"""SQLAlchemy asynchronous database primitives."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative metadata root for future migrations."""


def create_engine() -> AsyncEngine:
    """Build the application async engine without opening a connection."""
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True)


engine = create_engine()
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield one request-scoped asynchronous database session."""
    async with AsyncSessionFactory() as session:
        yield session


@asynccontextmanager
async def session_transaction(session: AsyncSession) -> AsyncIterator[None]:
    """Own a transaction or compose safely inside an existing request transaction."""
    if session.in_transaction():
        async with session.begin_nested():
            yield
    else:
        async with session.begin():
            yield
