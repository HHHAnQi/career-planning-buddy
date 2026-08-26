"""Retrieval evaluation: Recall@K / MRR / nDCG across retrieval modes.

Provisions the frozen ``retrieval-v1`` golden dataset for a dedicated
eval user, then measures three modes:

* ``vector``   — pgvector cosine only
* ``lexical``  — pg_trgm similarity only
* ``hybrid``   — both channels fused with RRF (production recall path)

plus reranking on top of hybrid (the production second stage). Reports
per-mode averages; the honest comparison table for RAG design reviews.

Usage::

    python -m scripts.run_retrieval_eval            # against local DB
    python -m scripts.run_retrieval_eval --k 5

Embedding quality depends on the configured provider: with the Mock
hash embedding the vector channel is noise (hybrid ≈ lexical), with the
local BGE provider it is semantic. The report records the provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.database import session_transaction
from app.models.user import User
from app.providers.embedding import build_embedding_provider
from app.providers.rerank import build_rerank_provider
from app.repositories.rag_documents import RagDocumentRepository
from app.services.rag_documents import RagDocumentService
from evals.retrieval_metrics import summarize

DEFAULT_DATASET = "retrieval-v1"
DATASET_PATHS = {
    "retrieval-v1": "evals/datasets/retrieval-v1.jsonl",
    "retrieval-v2": "evals/datasets/retrieval-v2.jsonl",
}
MODES = ("vector", "lexical", "hybrid")


def load_cases(dataset: str = DEFAULT_DATASET) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    with open(DATASET_PATHS[dataset], encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


async def run(k: int, dataset: str = DEFAULT_DATASET) -> dict[str, object]:
    settings = get_settings()
    cases = load_cases(dataset)
    embedding = build_embedding_provider(settings)
    rerank = build_rerank_provider(settings)
    engine = create_async_engine(settings.database_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            async with session_transaction(session):
                eval_user = User(
                    auth_type="guest",
                    guest_device_hash=uuid4().hex,
                    display_name="retrieval-eval",
                )
                session.add(eval_user)
                await session.flush()
                service = RagDocumentService(
                    session,
                    embedding_provider=embedding,
                    rerank_provider=rerank,
                    min_rerank_score=settings.rag_min_rerank_score,
                )
                # Each case provisions its OWN isolated user: a shared
                # user accumulates corpora across cases, and the lexical
                # enumeration (channel_k) then misses golden chunks whose
                # queries are deliberately paraphrased (v2 hardening).
                query_vectors: dict[str, list[float] | None] = {}
                provisioned: dict[str, list[object]] = {}
                corpus_users: dict[str, object] = {}
                for case in cases:
                    case_id = str(case["case_id"])
                    corpus_user = User(
                        auth_type="guest",
                        guest_device_hash=uuid4().hex,
                        display_name=f"retrieval-eval-{case_id}",
                    )
                    session.add(corpus_user)
                    await session.flush()
                    corpus_users[case_id] = corpus_user
                    all_chunks: list[object] = []
                    for doc in case["docs"]:  # type: ignore[index]
                        source_id = uuid4()
                        await service.ingest_document(
                            user_id=corpus_user.id,
                            doc_kind=str(doc["doc_kind"]),
                            source_id=source_id,
                            title=str(doc["title"]),
                            text=str(doc["text"]),
                        )
                    repository = RagDocumentRepository(session)
                    rows = await repository.hybrid_search(
                        user_id=corpus_user.id,
                        query_text=str(case["query"]),
                        query_vector=None,
                        limit=200,
                        mode="lexical",
                    )
                    for row in rows:
                        all_chunks.append(row.chunk)
                    provisioned[case_id] = all_chunks
                    vectors = await embedding.embed([str(case["query"])])
                    query_vectors[case_id] = vectors[0] if vectors else None

                results: dict[str, dict[str, float]] = {}
                for mode in MODES + ("hybrid_rerank",):
                    per_case: list[tuple[list[object], set[object]]] = []
                    repository = RagDocumentRepository(session)
                    for case in cases:
                        case_id = str(case["case_id"])
                        chunks = provisioned[case_id]
                        relevant = {
                            chunk.id
                            for chunk in chunks
                            if any(
                                marker in (chunk.content or "")
                                for marker in case["relevant"]  # type: ignore[index]
                            )
                        }
                        if not relevant:
                            raise ValueError(
                                f"{case_id}: no golden marker matched any chunk"
                            )
                        if mode == "hybrid_rerank":
                            outcome = await service.search(
                                user_id=corpus_users[case_id].id,  # type: ignore[attr-defined]
                                query=str(case["query"]),
                                limit=k,
                            )
                            ranked = [result.chunk.id for result in outcome.results]
                        else:
                            rows = await repository.hybrid_search(
                                user_id=corpus_users[case_id].id,
                                query_text=str(case["query"]),
                                query_vector=query_vectors[case_id],
                                limit=k,
                                mode=mode,
                            )
                            ranked = [row.chunk.id for row in rows]
                        per_case.append((ranked, relevant))
                    results[mode] = summarize(per_case, k=k)

                return {
                    "dataset": dataset,
                    "case_count": len(cases),
                    "k": k,
                    "embedding_provider": embedding.provider_name,
                    "rerank_provider": rerank.provider_name,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "modes": results,
                }
    finally:
        for provider in (embedding, rerank):
            close = getattr(provider, "aclose", None)
            if callable(close):
                await close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--dataset", choices=list(DATASET_PATHS), default=DEFAULT_DATASET)
    arguments = parser.parse_args()
    report = asyncio.run(run(arguments.k, arguments.dataset))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
