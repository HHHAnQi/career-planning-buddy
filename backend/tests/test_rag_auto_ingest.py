"""Tests for automatic RAG ingestion on resume/JD creation.

Pins:
* Creating a resume version through the API indexes its chunks
  (doc_kind=resume) for the owning user.
* Creating a job target indexes its JD chunks (doc_kind=job_target).
* Idempotent replay of the same creation request does not duplicate
  chunks (delete-then-insert ingest).
* A failing embedding provider never fails the creation itself — the
  document is still stored and lexically searchable.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ProviderUnavailableError
from app.models.rag_documents import RagDocumentChunk
from tests.test_profile_api import bearer, guest_login

RESUME_TEXT = """# 项目经历
FastAPI 求职规划系统：受控 Agent 状态机，预算守卫与租约恢复。

# 技能
Python、FastAPI、SQLAlchemy。"""

JD_TEXT = "负责 Python 后端服务开发，要求 FastAPI、PostgreSQL 与自动化测试经验，熟悉分布式系统。"


async def _chunks(db_session: AsyncSession, user_id: UUID) -> list[RagDocumentChunk]:
    return list(
        await db_session.scalars(
            select(RagDocumentChunk).where(RagDocumentChunk.user_id == user_id)
        )
    )


@pytest.mark.asyncio
async def test_resume_creation_auto_ingests_chunks(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, user_id_raw, _ = await guest_login(api_client)
    response = await api_client.post(
        "/api/v1/resume-versions",
        json={"label": "后端简历", "source_text": RESUME_TEXT},
        headers={**bearer(token), "Idempotency-Key": "rag-auto-ingest-1"},
    )
    assert response.status_code == 201

    chunks = await _chunks(db_session, UUID(user_id_raw))
    assert chunks
    assert all(chunk.doc_kind == "resume" for chunk in chunks)
    assert any("FastAPI" in chunk.content for chunk in chunks)
    # Section-aware chunking produced one chunk per heading section.
    assert len(chunks) == 2

    # Idempotent replay: same key, same request → no duplicates.
    replay = await api_client.post(
        "/api/v1/resume-versions",
        json={"label": "后端简历", "source_text": RESUME_TEXT},
        headers={**bearer(token), "Idempotency-Key": "rag-auto-ingest-1"},
    )
    assert replay.status_code in {200, 201}
    assert len(await _chunks(db_session, UUID(user_id_raw))) == 2


@pytest.mark.asyncio
async def test_job_target_creation_auto_ingests_chunks(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    token, user_id_raw, _ = await guest_login(api_client)
    response = await api_client.post(
        "/api/v1/job-targets",
        json={"title": "后端工程师", "company": "Example", "jd_text": JD_TEXT},
        headers={**bearer(token), "Idempotency-Key": "rag-auto-ingest-2"},
    )
    assert response.status_code == 201
    chunks = await _chunks(db_session, UUID(user_id_raw))
    assert chunks
    assert all(chunk.doc_kind == "job_target" for chunk in chunks)
    assert any("FastAPI" in chunk.content for chunk in chunks)


@pytest.mark.asyncio
async def test_embedding_failure_never_fails_creation(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.embedding import MockEmbeddingProvider

    async def broken_embed(self: MockEmbeddingProvider, texts: list[str]) -> list[list[float]]:
        raise ProviderUnavailableError("embedding provider unavailable", retryable=True)

    monkeypatch.setattr(MockEmbeddingProvider, "embed", broken_embed)

    token, user_id_raw, _ = await guest_login(api_client)
    response = await api_client.post(
        "/api/v1/resume-versions",
        json={"label": "降级简历", "source_text": RESUME_TEXT},
        headers={**bearer(token), "Idempotency-Key": "rag-auto-ingest-3"},
    )
    # Creation succeeded; chunks persisted with embedding_failed=true and
    # remain lexically searchable.
    assert response.status_code == 201
    chunks = await _chunks(db_session, UUID(user_id_raw))
    assert chunks
    assert all(chunk.embedding is None for chunk in chunks)
    assert all(chunk.embedding_failed for chunk in chunks)
