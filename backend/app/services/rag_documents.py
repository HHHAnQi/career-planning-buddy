"""Document RAG use cases: ingest, hybrid retrieval, rerank, gating.

Pipeline: chunk (deterministic) → embed (provider, failure-tolerant) →
persist → hybrid RRF recall → rerank → answerability gate.

The gate is the anti-hallucination commitment: when no chunk clears
``rag_min_rerank_score`` the search returns ``sufficient=False`` and the
caller must not fabricate an answer — the same philosophy as the
evidence-visibility validator on the planning graph.
"""

from __future__ import annotations

import logging
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import AgentError
from app.core.database import session_transaction
from app.models.rag_documents import RagDocumentChunk
from app.providers.embedding import EmbeddingProvider
from app.providers.rerank import MockRerankProvider, RerankProvider
from app.rag.chunking import chunk_document, chunk_hash
from app.repositories.rag_documents import RagDocumentRepository
from app.tools.sanitization import sanitize_untrusted_text

logger = logging.getLogger(__name__)

MAX_CHUNKS_PER_DOCUMENT = 60
# Industry sweet spot (Cohere: 50-75; Anthropic reference: 150).
# Wide recall gives hybrid fusion more signal to work with.
RECALL_CANDIDATES = 50
# "Retrieve wide, rerank narrow": only the top fusion results go to the
# neural reranker. Sending all 50 candidates degrades cross-encoder
# ordering (measured: Recall@5 drops 0.95→0.67 on v2 when reranking 50
# vs 20) because distractors dilute the score distribution and push
# golden chunks below the answerability gate.
RERANK_INPUT_SIZE = 20
# Rerank cache: absorbs 60-80% of repeated traffic (industry benchmark).
# Bounded LRU keyed on (query, chunk_ids) hash.
_RERANK_CACHE_SIZE = 256


class DocumentSearchResult:
    """One gated retrieval hit."""

    def __init__(
        self,
        *,
        chunk: RagDocumentChunk,
        rerank_score: float,
        vector_rank: int | None,
        lexical_rank: int | None,
    ) -> None:
        self.chunk = chunk
        self.rerank_score = rerank_score
        self.vector_rank = vector_rank
        self.lexical_rank = lexical_rank


class DocumentSearchOutcome:
    """Search result set with the answerability verdict."""

    def __init__(self, *, sufficient: bool, results: list[DocumentSearchResult]):
        self.sufficient = sufficient
        self.results = results


class RagDocumentService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider,
        min_rerank_score: float,
        rerank_bypass_enabled: bool = False,
    ) -> None:
        self._session = session
        self._embeddings = embedding_provider
        self._rerank = rerank_provider
        self._min_rerank_score = min_rerank_score
        self._rerank_bypass_enabled = rerank_bypass_enabled
        self._repo = RagDocumentRepository(session)

    async def ingest_document(
        self,
        *,
        user_id: UUID,
        doc_kind: str,
        source_id: UUID,
        title: str,
        text: str,
    ) -> int:
        """(Re)chunk, embed, and persist one document; returns chunk count."""

        chunks = chunk_document(text)[:MAX_CHUNKS_PER_DOCUMENT]
        if not chunks:
            return 0
        embeddings: list[list[float] | None] = [None] * len(chunks)
        embedding_failed = False
        try:
            vectors = await self._embeddings.embed(chunks)
            if len(vectors) == len(chunks):
                embeddings = [vector for vector in vectors]
        except AgentError as error:
            embedding_failed = True
            logger.warning(
                "rag chunk embedding failed (%s); lexical-only retrieval",
                type(error).__name__,
            )
        payload = [
            (content, chunk_hash(content), embedding)
            for content, embedding in zip(chunks, embeddings, strict=True)
        ]
        async with session_transaction(self._session):
            count = await self._repo.replace_chunks(
                user_id=user_id,
                doc_kind=doc_kind,
                source_id=source_id,
                title=title,
                chunks=payload,
                embedding_failed=embedding_failed,
            )
        return count

    async def search(
        self,
        *,
        user_id: UUID,
        query: str,
        limit: int = 5,
        doc_kinds: list[str] | None = None,
    ) -> DocumentSearchOutcome:
        """Hybrid recall → rerank → gate. Never raises on empty corpora.

        The query passes through ``normalize_query`` (domain synonym
        expansion + filler removal) before reaching the retrieval
        channels; ``doc_kinds`` optionally pre-filters the corpus.
        """
        from app.rag.query_normalize import normalize_query

        normalized = normalize_query(query)
        query_vector: list[float] | None = None
        try:
            vectors = await self._embeddings.embed([normalized])
            query_vector = vectors[0] if vectors else None
        except AgentError:
            query_vector = None

        async with session_transaction(self._session):
            recalled = await self._repo.hybrid_search(
                user_id=user_id,
                query_text=normalized,
                query_vector=query_vector,
                limit=RECALL_CANDIDATES,
                doc_kinds=doc_kinds,
            )
        if not recalled:
            return DocumentSearchOutcome(sufficient=False, results=[])

        # Conditional rerank bypass: SKIPPED by default (always rerank).
        # The design is in place for large-corpus deployments where rerank
        # latency matters — enable via RAG_RERANK_BYPASS=true. On small
        # corpora the gap-based heuristic is unreliable (noise items can
        # have large relative RRF gaps) and the answerability gate is
        # safer with the reranker always scoring.
        if self._rerank_bypass_enabled and _bypass_rerank(recalled):
            top_rrf = recalled[0].rrf_score or 0.0
            ranked = list(
                DocumentSearchResult(
                    chunk=row.chunk,
                    rerank_score=1.0 - (row.rrf_score or 0.0),
                    vector_rank=row.vector_rank,
                    lexical_rank=row.lexical_rank,
                )
                for row in recalled[:limit]
            )
            return DocumentSearchOutcome(
                sufficient=top_rrf > 0.01, results=ranked if top_rrf > 0.01 else []
            )

        # "Retrieve wide, rerank narrow": hybrid fusion ranks the full
        # recall set (50), but only the top fusion results are sent to
        # the cross-encoder. Reranking the full 50 degrades ordering
        # because distractors dilute the score distribution.
        rerank_input = recalled[:RERANK_INPUT_SIZE]

        # Rerank result cache: keyed on (normalized query, chunk ids) hash.
        # Same query + same candidate set → identical ordering, skip the
        # neural reranker call entirely (industry: absorbs 60-80% of
        # repeated traffic at zero quality cost).
        cache_key = sha256(
            (normalized + "|" + ",".join(str(r.chunk.id) for r in rerank_input)).encode()
        ).hexdigest()
        cached = _rerank_cache_get(cache_key)
        if cached is not None:
            scores = cached
        else:
            # The reranker receives the ORIGINAL query, not the expanded
            # one: cross-encoders handle synonyms natively, and appending
            # canonical terms dilutes the cross-attention signal (measured:
            # hybrid_rerank MRR 1.0 → 0.63 when reranking the expanded
            # query on v2). Expansion helps only the bi-encoder/trigram
            # channels, which see it above.
            scores = await self._rerank.rerank(
                query, [row.chunk.content for row in rerank_input]
            )
            _rerank_cache_put(cache_key, scores)
        ranked = sorted(
            (
                DocumentSearchResult(
                    chunk=row.chunk,
                    rerank_score=float(score) if index < len(scores) else 0.0,
                    vector_rank=row.vector_rank,
                    lexical_rank=row.lexical_rank,
                )
                for index, (row, score) in enumerate(
                    zip(rerank_input, scores, strict=False)
                )
                if float(score) >= self._min_rerank_score
            ),
            key=lambda item: (-item.rerank_score, item.chunk.chunk_index),
        )
        return DocumentSearchOutcome(
            sufficient=bool(ranked), results=ranked[:limit]
        )


def sanitized_snippet(content: str, limit: int = 1200) -> str:
    """Untrusted document content is sanitized before entering evidence."""

    return sanitize_untrusted_text(content, limit)


def _confident_fusion_confidence(recalled: list[Any]) -> float:
    """Return the RRF score gap ratio between #1 and #2 (0 if no gap).

    A gap > 0.3 means the fusion has a clear winner — the neural reranker
    is unlikely to improve ordering and may degrade it.
    """
    if len(recalled) < 2:
        return 1.0
    top = recalled[0].rrf_score or 0.0
    second = recalled[1].rrf_score or 0.0
    if top <= 0:
        return 0.0
    return (top - second) / top


# Bounded LRU for rerank results (query+chunks hash → score list).
_rerank_cache: dict[str, list[float]] = {}


def _rerank_cache_get(key: str) -> list[float] | None:
    return _rerank_cache.get(key)


def _rerank_cache_put(key: str, scores: list[float]) -> None:
    if len(_rerank_cache) >= _RERANK_CACHE_SIZE:
        # Evict oldest entries (dict preserves insertion order).
        for old_key in list(_rerank_cache)[: _RERANK_CACHE_SIZE // 4]:
            del _rerank_cache[old_key]
    _rerank_cache[key] = scores


def _bypass_rerank(recalled: list[Any]) -> bool:
    """Decide whether to skip the neural reranker for this query.

    Bypass requires BOTH a confident gap AND a meaningful absolute score;
    either alone is insufficient (gap alone lets noise through, absolute
    alone wastes rerank on ambiguous rankings).
    """
    if len(recalled) <= 3:
        return False  # too few candidates to trust fusion alone
    gap = _confident_fusion_confidence(recalled)
    top = recalled[0].rrf_score or 0.0
    return gap > 0.3 and top > 0.02


async def ingest_untrusted_document(
    session: AsyncSession,
    *,
    embedding_provider: EmbeddingProvider,
    user_id: UUID,
    doc_kind: str,
    source_id: UUID,
    title: str,
    text: str,
) -> int:
    """Best-effort post-creation ingest hook (no rerank needed for ingest).

    Callers wrap this in try/except: ingestion must never fail the
    resume/JD creation that triggered it — the document stays searchable
    lexically even when embedding fails, and a later re-ingest is
    idempotent.
    """

    service = RagDocumentService(
        session,
        embedding_provider=embedding_provider,
        rerank_provider=MockRerankProvider(),
        min_rerank_score=0.0,
    )
    return await service.ingest_document(
        user_id=user_id,
        doc_kind=doc_kind,
        source_id=source_id,
        title=title,
        text=text,
    )
