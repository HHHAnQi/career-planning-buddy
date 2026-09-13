"""Persistence operations for user-owned memories and consent candidates."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import Memory, MemoryCandidate


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_memories(
        self,
        *,
        user_id: UUID,
        memory_type: str | None,
        status: str,
        include_sensitive: bool,
        cursor: UUID | None,
        limit: int,
    ) -> list[Memory]:
        statement: Select[tuple[Memory]] = select(Memory).where(
            Memory.user_id == user_id,
            Memory.status == status,
        )
        if memory_type is not None:
            statement = statement.where(Memory.memory_type == memory_type)
        if not include_sensitive:
            statement = statement.where(Memory.sensitivity == "normal")
        if cursor is not None:
            statement = statement.where(Memory.id > cursor)
        rows = await self._session.scalars(statement.order_by(Memory.id).limit(limit))
        return list(rows)

    async def get_memory(
        self, memory_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> Memory | None:
        statement = select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        memory = await self._session.scalar(statement)
        return memory if isinstance(memory, Memory) else None

    async def get_active_weakness(self, user_id: UUID, weakness_key: str) -> Memory | None:
        memory = await self._session.scalar(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == "active",
                Memory.content_json["weakness_key"].as_string() == weakness_key,
            )
            .order_by(Memory.updated_at.desc())
            .limit(1)
        )
        return memory if isinstance(memory, Memory) else None

    async def get_pending_weakness_candidate(
        self, user_id: UUID, weakness_key: str
    ) -> MemoryCandidate | None:
        candidate = await self._session.scalar(
            select(MemoryCandidate)
            .where(
                MemoryCandidate.user_id == user_id,
                MemoryCandidate.status == "pending",
                MemoryCandidate.content_json["weakness_key"].as_string() == weakness_key,
            )
            .order_by(MemoryCandidate.created_at.desc())
            .limit(1)
        )
        return candidate if isinstance(candidate, MemoryCandidate) else None

    async def delete_memory(self, memory: Memory) -> None:
        await self._session.delete(memory)
        await self._session.flush()

    async def list_candidates(
        self,
        *,
        user_id: UUID,
        status: str,
        cursor: UUID | None,
        limit: int,
    ) -> list[MemoryCandidate]:
        statement = select(MemoryCandidate).where(
            MemoryCandidate.user_id == user_id,
            MemoryCandidate.status == status,
        )
        if cursor is not None:
            statement = statement.where(MemoryCandidate.id > cursor)
        rows = await self._session.scalars(statement.order_by(MemoryCandidate.id).limit(limit))
        return list(rows)

    async def get_candidate(
        self, candidate_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> MemoryCandidate | None:
        statement = select(MemoryCandidate).where(
            MemoryCandidate.id == candidate_id,
            MemoryCandidate.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        candidate = await self._session.scalar(statement)
        return candidate if isinstance(candidate, MemoryCandidate) else None

    async def get_candidate_by_decision_key(
        self, user_id: UUID, idempotency_key: str
    ) -> MemoryCandidate | None:
        candidate = await self._session.scalar(
            select(MemoryCandidate).where(
                MemoryCandidate.user_id == user_id,
                MemoryCandidate.decision_idempotency_key == idempotency_key,
            )
        )
        return candidate if isinstance(candidate, MemoryCandidate) else None

    async def create_memory(
        self,
        *,
        user_id: UUID,
        memory_type: str,
        summary: str,
        content_json: dict[str, object],
        sensitivity: str,
        embedding: list[float] | None,
        source_run_id: UUID | None,
    ) -> Memory:
        memory = Memory(
            user_id=user_id,
            memory_type=memory_type,
            summary=summary,
            content_json=content_json,
            sensitivity=sensitivity,
            status="active",
            embedding=embedding,
            source_run_id=source_run_id,
        )
        self._session.add(memory)
        await self._session.flush()
        await self._session.refresh(memory)
        return memory

    async def create_candidate(
        self,
        *,
        user_id: UUID,
        memory_type: str,
        summary: str,
        content_json: dict[str, object],
        sensitivity: str,
        proposed_by_run_id: UUID | None,
        expires_at: datetime,
    ) -> MemoryCandidate:
        candidate = MemoryCandidate(
            user_id=user_id,
            memory_type=memory_type,
            summary=summary,
            content_json=content_json,
            sensitivity=sensitivity,
            status="pending",
            proposed_by_run_id=proposed_by_run_id,
            expires_at=expires_at,
        )
        self._session.add(candidate)
        await self._session.flush()
        await self._session.refresh(candidate)
        return candidate

    async def candidate_exists_for_review(
        self,
        *,
        user_id: UUID,
        review_id: UUID,
        memory_type: str,
        normalized_summary: str,
    ) -> bool:
        candidate_id = await self._session.scalar(
            select(MemoryCandidate.id)
            .where(
                MemoryCandidate.user_id == user_id,
                MemoryCandidate.memory_type == memory_type,
                MemoryCandidate.content_json["source_review_id"].as_string() == str(review_id),
                MemoryCandidate.content_json["normalized_summary"].as_string()
                == normalized_summary,
            )
            .limit(1)
        )
        return candidate_id is not None

    async def delete_pending_candidates_for_review(
        self, *, user_id: UUID, review_id: UUID
    ) -> None:
        await self._session.execute(
            delete(MemoryCandidate).where(
                MemoryCandidate.user_id == user_id,
                MemoryCandidate.status == "pending",
                MemoryCandidate.content_json["source_review_id"].as_string()
                == str(review_id),
            )
        )
        await self._session.flush()

    @staticmethod
    def expire_if_needed(candidate: MemoryCandidate) -> None:
        if candidate.status == "pending" and candidate.expires_at <= datetime.now(UTC):
            candidate.status = "expired"
            candidate.decided_at = datetime.now(UTC)
