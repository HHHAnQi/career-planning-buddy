"""Async SQLAlchemy infrastructure tests."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionFactory


@pytest.mark.asyncio
async def test_async_session_factory_creates_async_session() -> None:
    session = AsyncSessionFactory()
    try:
        assert isinstance(session, AsyncSession)
    finally:
        await session.close()
