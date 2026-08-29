"""Vector and evidence persistence queries used by read-only Stage 4 Tools."""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence import ExperienceAtom, ExperienceAtomCandidate, Memory, SearchSource
from app.models.rag_documents import RagDocumentChunk


class EvidenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pinned_memories(self, user_id: UUID, *, limit: int = 3) -> list[Memory]:
        rows = await self._session.scalars(
            select(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == "active",
                Memory.content_json["pinned"].as_boolean().is_(True),
            )
            .order_by(Memory.updated_at.desc())
            .limit(limit)
        )
        return list(rows)

    async def memory_lookup(
        self,
        *,
        user_id: UUID,
        query: str,
        vector: list[float] | None,
        limit: int,
    ) -> list[tuple[Memory, float]]:
        if vector is not None:
            distance = Memory.embedding.cosine_distance(vector)
            vector_rows = await self._session.execute(
                select(Memory, (1 - distance).label("score"))
                .where(
                    Memory.user_id == user_id,
                    Memory.status == "active",
                    Memory.embedding.is_not(None),
                )
                .order_by(distance)
                .limit(limit)
            )
            return [(memory, max(0.0, min(1.0, float(score)))) for memory, score in vector_rows]
        terms = [term for term in query.split() if term][:5]
        statement = select(Memory).where(
            Memory.user_id == user_id,
            Memory.status == "active",
        )
        if terms:
            statement = statement.where(or_(*(Memory.summary.ilike(f"%{term}%") for term in terms)))
        memory_rows = await self._session.scalars(
            statement.order_by(Memory.updated_at.desc()).limit(limit)
        )
        return [(memory, 0.5) for memory in memory_rows]

    async def touch_memories(
        self,
        *,
        user_id: UUID,
        memory_ids: list[UUID],
        used_at: datetime,
    ) -> None:
        if not memory_ids:
            return
        await self._session.execute(
            update(Memory)
            .where(
                Memory.user_id == user_id,
                Memory.status == "active",
                Memory.id.in_(memory_ids),
            )
            .values(last_used_at=used_at)
        )
        await self._session.flush()

    async def rag_retrieve(
        self,
        *,
        goal_type: str,
        vector: list[float],
        limit: int,
        min_similarity: float = 0.35,
    ) -> list[tuple[ExperienceAtom, float]]:
        distance = ExperienceAtom.embedding.cosine_distance(vector)
        rows = await self._session.execute(
            select(ExperienceAtom, (1 - distance).label("score"))
            .where(
                ExperienceAtom.goal_type == goal_type,
                ExperienceAtom.is_active.is_(True),
                ExperienceAtom.embedding.is_not(None),
                distance <= 1 - min_similarity,
            )
            .order_by(distance)
            .limit(limit)
        )
        return [(atom, max(0.0, min(1.0, float(score)))) for atom, score in rows]

    async def upsert_search_source(
        self,
        *,
        run_id: UUID,
        url: str,
        url_hash: str,
        content_hash: str,
        title: str | None,
        snippet: str,
        source_type: str,
        reliability: float,
        provider: str,
        retrieved_at: datetime,
        provider_request_id: str | None = None,
        published_at: datetime | None = None,
    ) -> SearchSource:
        statement = (
            insert(SearchSource)
            .values(
                run_id=run_id,
                url=url,
                canonical_url=url,
                url_hash=url_hash,
                title=title,
                snippet=snippet,
                source_type=source_type,
                reliability=Decimal(str(reliability)),
                provider=provider,
                content_hash=content_hash,
                provider_request_id=provider_request_id,
                published_at=published_at,
                retrieved_at=retrieved_at,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "url_hash"])
        )
        await self._session.execute(statement)
        source = await self._session.scalar(
            select(SearchSource).where(
                SearchSource.run_id == run_id, SearchSource.url_hash == url_hash
            )
        )
        if source is None:
            raise RuntimeError("SearchSource upsert did not return a row")
        return source

    async def search_sources_for_run(self, run_id: UUID) -> list[SearchSource]:
        rows = await self._session.scalars(
            select(SearchSource)
            .where(SearchSource.run_id == run_id)
            .order_by(SearchSource.retrieved_at, SearchSource.id)
        )
        return list(rows)

    async def sources_by_ids(self, *, run_id: UUID, source_ids: list[UUID]) -> list[SearchSource]:
        if not source_ids:
            return []
        rows = await self._session.scalars(
            select(SearchSource).where(
                SearchSource.run_id == run_id, SearchSource.id.in_(source_ids)
            )
        )
        return list(rows)

    async def add_experience_candidate(
        self, candidate: ExperienceAtomCandidate
    ) -> ExperienceAtomCandidate | None:
        statement = (
            insert(ExperienceAtomCandidate)
            .values(
                goal_type=candidate.goal_type,
                title=candidate.title,
                content=candidate.content,
                source_ids=candidate.source_ids,
                evidence_excerpt=candidate.evidence_excerpt,
                confidence=candidate.confidence,
                content_hash=candidate.content_hash,
                status=candidate.status,
                proposed_by_run_id=candidate.proposed_by_run_id,
                expires_at=candidate.expires_at,
            )
            .on_conflict_do_nothing(index_elements=["content_hash"])
            .returning(ExperienceAtomCandidate)
        )
        row = await self._session.scalar(statement)
        return row if isinstance(row, ExperienceAtomCandidate) else None

    async def candidate_for_update(self, candidate_id: UUID) -> ExperienceAtomCandidate | None:
        row = await self._session.scalar(
            select(ExperienceAtomCandidate)
            .where(ExperienceAtomCandidate.id == candidate_id)
            .with_for_update()
        )
        return row if isinstance(row, ExperienceAtomCandidate) else None

    async def list_candidates(self, status: str = "pending") -> list[ExperienceAtomCandidate]:
        rows = await self._session.scalars(
            select(ExperienceAtomCandidate)
            .where(ExperienceAtomCandidate.status == status)
            .order_by(ExperienceAtomCandidate.created_at, ExperienceAtomCandidate.id)
        )
        return list(rows)

    async def evidence_ids_for_run(
        self,
        *,
        run_id: UUID,
        user_id: UUID,
    ) -> set[tuple[str, UUID]]:
        catalog: set[tuple[str, UUID]] = set()
        memory_ids = await self._session.scalars(
            select(Memory.id).where(Memory.user_id == user_id, Memory.status == "active")
        )
        catalog.update(("memory", memory_id) for memory_id in memory_ids)
        source_ids = await self._session.scalars(
            select(SearchSource.id).where(SearchSource.run_id == run_id)
        )
        catalog.update(("search_source", source_id) for source_id in source_ids)
        tool_rows = await self._session.execute(
            select(ExperienceAtom.id).where(ExperienceAtom.is_active.is_(True))
        )
        catalog.update(("experience_atom", atom_id) for atom_id in tool_rows.scalars())
        chunk_ids = await self._session.scalars(
            select(RagDocumentChunk.id).where(RagDocumentChunk.user_id == user_id)
        )
        catalog.update(("rag_document_chunk", chunk_id) for chunk_id in chunk_ids)
        return catalog

    async def resolve_memories(self, user_id: UUID, ids: Iterable[UUID]) -> list[Memory]:
        values = list(ids)
        if not values:
            return []
        rows = await self._session.scalars(
            select(Memory).where(Memory.user_id == user_id, Memory.id.in_(values))
        )
        return list(rows)

    async def resolve_atoms(self, ids: Iterable[UUID]) -> list[ExperienceAtom]:
        values = list(ids)
        if not values:
            return []
        rows = await self._session.scalars(
            select(ExperienceAtom).where(ExperienceAtom.id.in_(values))
        )
        return list(rows)

    async def resolve_sources(self, run_id: UUID, ids: Iterable[UUID]) -> list[SearchSource]:
        values = list(ids)
        if not values:
            return []
        rows = await self._session.scalars(
            select(SearchSource).where(SearchSource.run_id == run_id, SearchSource.id.in_(values))
        )
        return list(rows)
