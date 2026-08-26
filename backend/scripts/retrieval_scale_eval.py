"""Retrieval scale experiments — does the small-corpus conclusion hold at
100 / 500 / 2000 chunks per corpus?

Generates reproducible synthetic corpora (seeded RNG): realistic resume/JD
documents assembled from skill/project/company pools, with same-domain
distractors and paraphrase-style queries whose golden targets are known
by construction. Then measures Recall@5 / MRR across:

* vector (bge-m3 cosine)        — plus HNSW-vs-exact comparison
* lexical (pg_trgm)
* hybrid RRF at channel_k 20 / 50 / 200
* hybrid + neural rerank (GPU bge-reranker) over top-20 / top-50

Usage::

    python -m scripts.retrieval_scale_eval --sizes 100 500 2000
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.database import session_transaction
from app.core.security import TokenService
from app.providers.embedding import EmbeddingProvider
from app.providers.rerank import RerankProvider
from app.rag.chunking import chunk_document
from app.repositories.rag_documents import RagDocumentRepository
from app.services.auth import AuthService
from evals.retrieval_metrics import mrr, recall_at_k

SKILL_POOL = [
    ("FastAPI", "接口开发与服务治理"), ("LangGraph", "智能体状态机编排"),
    ("pgvector", "混合检索与重排"), ("React", "组件库与前端工程化"),
    ("K8s", "容器编排与弹性伸缩"), ("Flink", "实时计算与状态管理"),
    ("PyTorch", "模型训练与部署"), ("Spark", "离线数仓建设"),
]
DOMAIN_POOL = [
    "电商交易", "内容社区", "在线教育", "企业服务",
    "游戏", "金融风控", "物流调度", "医疗信息化",
]
COMPANY_POOL = [
    "星尘科技", "蓝鲸信息", "云帆数据", "极光智能",
    "磐石系统", "启明网络", "浪潮软件", "天工算法",
]

TEMPLATES = [
    "{domain}平台核心服务：{skill}{detail}，支撑日均{scale}万请求。",
    "负责{domain}方向的{skill}落地，{detail}，故障率下降{pct}%。",
    "从零搭建{domain}的{skill}体系，{detail}，团队效率提升明显。",
    "{domain}性能专项：{skill}调优{detail}，P99 延迟降至{ms}ms。",
]
DETAILS = ["含灰度发布与回滚方案", "覆盖监控告警与容量规划", "支持多租户隔离", "接入全链路追踪", "完成压力测试与容量模型"]
QUERY_TEMPLATES = [
    "做过{skill}相关的项目吗",
    "有{domain}方向的经验吗",
    "{skill}落地效果怎么样",
    "找熟悉{domain}和{skill}的候选人",
]


def _gen_corpus(rng: random.Random, target_chunks: int) -> tuple[list[str], list[tuple[str, str]]]:
    """Returns (documents, queries with golden content marker).

    Golden documents get a UNIQUE (skill, domain) combination so the
    query semantically identifies exactly one document — reusing combos
    would make several chunks equally valid answers while only the
    marked one counts, deflating the metric unfairly.
    """
    docs: list[str] = []
    queries: list[tuple[str, str]] = []
    marker_counter = 0
    used_combos: set[tuple[str, str]] = set()
    while sum(len(chunk_document(d)) for d in docs) < target_chunks:
        skill, detail = rng.choice(SKILL_POOL)
        domain = rng.choice(DOMAIN_POOL)
        company = rng.choice(COMPANY_POOL)
        is_golden_candidate = (marker_counter + 1) % 3 == 0
        if is_golden_candidate:
            if (skill[0], domain) in used_combos:
                continue  # golden combos stay unique
            used_combos.add((skill[0], domain))
        # distractor docs may reuse combos freely (they add noise, not
        # ambiguity, because only the golden doc's (skill, domain) is
        # query-identifiable via the unique-combo reservation)
        sections = [
            f"# {company}·{domain}项目",
        ]
        for _ in range(rng.randint(3, 5)):
            template = rng.choice(TEMPLATES)
            body = template.format(
                domain=domain, skill=skill, detail=rng.choice(DETAILS),
                scale=rng.randint(5, 500), pct=rng.randint(10, 60), ms=rng.randint(5, 200),
            )
            sections.append(body)
        docs.append("\n\n".join(sections))
        # every 3rd doc becomes a query target with a unique marker
        marker_counter += 1
        if marker_counter % 3 == 0:
            marker = f"专项编号S{marker_counter:04d}"
            sections.append(f"备注：本段属于{marker}性能专项记录。")
            docs[-1] = "\n\n".join(sections)
            queries.append((rng.choice(QUERY_TEMPLATES).format(skill=skill, domain=domain), marker))
    return docs, queries


async def _provision(
    factory: async_sessionmaker[AsyncSession],
    docs: list[str],
    embedding: EmbeddingProvider,
) -> UUID:
    async with factory() as session:
        async with session_transaction(session):
            user = (await AuthService(session, TokenService(get_settings())).login_guest(None)).user
            chunks_all: list[str] = []
            for i, doc in enumerate(docs):
                chunks_all.extend(chunk_document(doc))
            vectors = await embedding.embed(chunks_all)
            repo = RagDocumentRepository(session)
            await repo.replace_chunks(
                user_id=user.id,
                doc_kind="resume",
                source_id=user.id,  # single doc-stream corpus
                title="scale-corpus",
                chunks=[
                    (content, f"hash-{idx}", vector)
                    for idx, (content, vector) in enumerate(zip(chunks_all, vectors, strict=True))
                ],
            )
            return user.id


async def run_size(factory, size: int, embedding: EmbeddingProvider, reranker: RerankProvider) -> dict[str, object]:
    rng = random.Random(42 + size)
    docs, queries = _gen_corpus(rng, size)
    started = time.monotonic()
    user_id = await _provision(factory, docs, embedding)
    provision_s = time.monotonic() - started

    async with factory() as session:
        async with session_transaction(session):
            repo = RagDocumentRepository(session)
            all_rows = await repo.hybrid_search(
                user_id=user_id, query_text="项目", query_vector=None, limit=5000, channel_k=5000, mode="lexical",
            )
            total_chunks = len(all_rows)
            # Golden sets come from the full corpus (by construction), never
            # from what a retrieval channel happens to return.
            golden_by_marker = {row.chunk.content: row.chunk.id for row in all_rows}
            valid_queries = [
                (query, marker)
                for query, marker in queries
                if any(marker in content for content in golden_by_marker)
            ]
            queries = valid_queries

            results: dict[str, object] = {"corpus_chunks": total_chunks, "queries": len(queries), "provision_s": round(provision_s, 1)}
            for mode, channel_k in (("vector", 200), ("lexical", 200), ("hybrid20", 20), ("hybrid200", 200)):
                hits: list[tuple[list[UUID], str]] = []
                q_start = time.monotonic()
                for query, marker in queries:
                    vector = None
                    if mode.startswith(("vector", "hybrid")):
                        vectors = await embedding.embed([query])
                        vector = vectors[0]
                    rows = await repo.hybrid_search(
                        user_id=user_id, query_text=query, query_vector=vector,
                        limit=5, channel_k=channel_k, mode=mode if mode != "hybrid20" else "hybrid",
                    )
                    ranked = [r.chunk.id for r in rows]
                    golden_ids = {
                        cid for content, cid in golden_by_marker.items() if marker in content
                    }
                    hits.append((ranked, golden_ids))
                elapsed = time.monotonic() - q_start
                pairs = [(ranked, set(g)) for ranked, g in hits]
                results[mode] = {
                    "recall_at_5": round(sum(recall_at_k(r, g, 5) for r, g in pairs) / len(pairs), 3),
                    "mrr": round(sum(mrr(r, g) for r, g in pairs) / len(pairs), 3),
                    "query_ms": round(elapsed * 1000 / len(queries)),
                }

            # hybrid + rerank over top-20
            for topn in (20, 50):
                pairs = []
                q_start = time.monotonic()
                for query, marker in queries:
                    vectors = await embedding.embed([query])
                    rows = await repo.hybrid_search(
                        user_id=user_id, query_text=query, query_vector=vectors[0],
                        limit=topn, channel_k=200, mode="hybrid",
                    )
                    if rows:
                        scores = await reranker.rerank(query, [r.chunk.content for r in rows])
                        order = sorted(range(len(rows)), key=lambda i: -scores[i])
                        ranked = [rows[i].chunk.id for i in order][:5]
                    else:
                        ranked = []
                    golden_ids = {
                        cid for content, cid in golden_by_marker.items() if marker in content
                    }
                    pairs.append((ranked, golden_ids))
                elapsed = time.monotonic() - q_start
                results[f"hybrid+rerank{topn}"] = {
                    "recall_at_5": round(sum(recall_at_k(r, g, 5) for r, g in pairs) / len(pairs), 3),
                    "mrr": round(sum(mrr(r, g) for r, g in pairs) / len(pairs), 3),
                    "query_ms": round(elapsed * 1000 / len(queries)),
                }
            return results


async def main_async(sizes: list[int]) -> dict[str, object]:
    settings = get_settings()
    from app.providers.embedding import build_embedding_provider
    from app.providers.rerank import build_rerank_provider

    embedding = build_embedding_provider(settings)
    reranker = build_rerank_provider(settings)
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return {
            str(size): await run_size(factory, size, embedding, reranker)
            for size in sizes
        }
    finally:
        close = getattr(embedding, "aclose", None)
        if callable(close):
            await close()
        close_r = getattr(reranker, "aclose", None)
        if callable(close_r):
            await close_r()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", nargs="*", type=int, default=[100, 500, 2000])
    args = parser.parse_args()
    import json

    print(json.dumps(asyncio.run(main_async(args.sizes)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
