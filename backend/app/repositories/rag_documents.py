"""Hybrid retrieval over ``rag_document_chunks`` with Reciprocal Rank Fusion.

Two channels run as SQL top-K queries per user:

* semantic — pgvector cosine distance (``embedding <=> :query_vector``)
* lexical — pg_trgm similarity (CJK-safe without zhparser)

The channels are fused with Reciprocal Rank Fusion
(``score = Σ 1 / (k + rank)`` with the standard ``k = 60``), which needs
no score normalization across incomparable scales — exactly why RRF is
the standard hybrid-retrieval fusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_documents import RagDocumentChunk

RRF_K = 60


@dataclass(frozen=True, slots=True)
class HybridSearchRow:
    chunk: RagDocumentChunk
    rrf_score: float
    # 1-based rank inside each channel; None when the channel did not
    # return the chunk.
    vector_rank: int | None
    lexical_rank: int | None


class RagDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_chunks(
        self,
        *,
        user_id: UUID,
        doc_kind: str,
        source_id: UUID,
        title: str,
        chunks: list[tuple[str, str, list[float] | None]],
        embedding_failed: bool = False,
    ) -> int:
        """Idempotently (re)ingest one document: delete-then-insert.

        ``chunks`` is a list of ``(content, content_hash, embedding)``;
        the chunk_index is the list position. Returns the stored count.
        """

        await self._session.execute(
            delete(RagDocumentChunk).where(
                RagDocumentChunk.user_id == user_id,
                RagDocumentChunk.source_id == source_id,
            )
        )
        rows = [
            RagDocumentChunk(
                user_id=user_id,
                doc_kind=doc_kind,
                source_id=source_id,
                title=title[:300],
                chunk_index=index,
                content=content,
                content_hash=content_hash,
                embedding=embedding,
                embedding_failed=embedding_failed and embedding is None,
                meta_json={"chunker": "structure-aware-v1"},
            )
            for index, (content, content_hash, embedding) in enumerate(chunks)
        ]
        self._session.add_all(rows)
        await self._session.flush()
        return len(rows)

    async def hybrid_search(
        self,
        *,
        user_id: UUID,
        query_text: str,
        query_vector: list[float] | None,
        limit: int,
        channel_k: int = 50,
        mode: str = "hybrid",
        doc_kinds: list[str] | None = None,
    ) -> list[HybridSearchRow]:
        """Fuse pgvector and pg_trgm rankings with RRF; user-isolated.

        ``mode`` selects the recall channels for retrieval evaluation:
        ``hybrid`` (default), ``vector`` (semantic only), ``lexical``
        (pg_trgm only). ``doc_kinds`` optionally pre-filters the corpus
        by document type (resume/job_target) BEFORE retrieval — industry
        practice for narrowing the search space (filter governs
        eligibility, rank governs relevance).
        """
        # pgvector's HNSW default ef_search=40 silently truncates recall;
        # industry practice is 100-200 (pgvector docs, Qdrant benchmarks).
        # Session-scoped: affects only queries on this connection.
        await self._session.execute(text("SET LOCAL hnsw.ef_search = 200"))

        base_filters = [RagDocumentChunk.user_id == user_id]
        if doc_kinds:
            base_filters.append(RagDocumentChunk.doc_kind.in_(doc_kinds))

        vector_ranks: dict[UUID, int] = {}
        if query_vector is not None and mode in {"hybrid", "vector"}:
            distance = RagDocumentChunk.embedding.cosine_distance(query_vector)
            vector_rows = await self._session.scalars(
                select(RagDocumentChunk)
                .where(
                    *base_filters,
                    RagDocumentChunk.embedding.is_not(None),
                )
                .order_by(distance)
                .limit(channel_k)
            )
            for rank, chunk in enumerate(vector_rows, start=1):
                vector_ranks[chunk.id] = rank

        lexical_ranks: dict[UUID, int] = {}
        if mode in {"hybrid", "lexical"}:
            lexical_rows = await self._session.scalars(
                select(RagDocumentChunk)
                .where(*base_filters)
                .order_by(func.similarity(RagDocumentChunk.content, query_text).desc())
                .limit(channel_k)
            )
            for rank, chunk in enumerate(lexical_rows, start=1):
                lexical_ranks[chunk.id] = rank

        candidates = set(vector_ranks) | set(lexical_ranks)
        if not candidates:
            return []
        by_id = {
            chunk.id: chunk
            for chunk in await self._session.scalars(
                select(RagDocumentChunk).where(
                    *base_filters,
                    RagDocumentChunk.id.in_(candidates),
                )
            )
        }
        fused: list[HybridSearchRow] = []
        for chunk_id in candidates:
            candidate: RagDocumentChunk | None = by_id.get(chunk_id)
            if candidate is None:
                continue
            score = 0.0
            vector_rank = vector_ranks.get(chunk_id)
            lexical_rank = lexical_ranks.get(chunk_id)
            if vector_rank is not None:
                score += 1 / (RRF_K + vector_rank)
            if lexical_rank is not None:
                score += 1 / (RRF_K + lexical_rank)
            fused.append(
                HybridSearchRow(
                    chunk=candidate,
                    rrf_score=score,
                    vector_rank=vector_rank,
                    lexical_rank=lexical_rank,
                )
            )
        fused.sort(key=lambda row: (-row.rrf_score, row.chunk.chunk_index))
        return fused[:limit]
