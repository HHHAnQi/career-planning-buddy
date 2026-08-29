"""Persisted real-provider Stage 6B chain with secret-safe metrics only."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import AsyncSessionFactory, session_transaction
from app.models.agent_run import AgentRun
from app.models.user import User
from app.providers.embedding import build_embedding_provider
from app.providers.evidence_distillation import build_evidence_distillation_provider
from app.providers.search import BaiduSearchProvider, build_search_provider
from app.repositories.evidence import EvidenceRepository
from app.services.experience_atoms import ExperienceAtomService
from app.tools.executors import _clean, _normalize_url


async def main() -> None:
    settings = get_settings()
    search = build_search_provider(settings)
    if not isinstance(search, BaiduSearchProvider):
        raise RuntimeError("SEARCH_PROVIDER must be baidu")
    embedding = build_embedding_provider(settings)
    distillation = build_evidence_distillation_provider(settings)
    started = monotonic()
    async with AsyncSessionFactory() as session:
        async with session_transaction(session):
            user = User(guest_device_hash=sha256(uuid4().bytes).hexdigest())
            session.add(user)
            await session.flush()
            run = AgentRun(
                user_id=user.id,
                idempotency_key=f"stage6b-real-{uuid4().hex[:16]}",
                request_text="Stage 6B real provider validation",
                hint_intent="create_plan",
                status="failed",
                error_code="VALIDATION_SOURCE_RUN",
                graph_version=settings.agent_graph_version,
                config_snapshot_json={},
                deadline_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(run)
            await session.flush()
            run_id = run.id

        raw_rows = []
        request_ids: list[object] = []
        for query, freshness in (
            ("2026 AI 后端岗位技能要求", None),
            ("今天 AI Agent 招聘市场最新变化", 7),
        ):
            raw_rows.extend(await search.search(query=query, limit=5, freshness_days=freshness))
            request_ids.append(search.last_trace.get("request_id"))

        async with session_transaction(session):
            repository = EvidenceRepository(session)
            for row in raw_rows:
                url = _normalize_url(row.url)
                snippet = _clean(row.snippet, 1200)
                if not url or not snippet:
                    continue
                await repository.upsert_search_source(
                    run_id=run_id,
                    url=url,
                    url_hash=sha256(url.encode()).hexdigest(),
                    content_hash=sha256(snippet.encode()).hexdigest(),
                    title=_clean(row.title or "Untitled source", 300),
                    snippet=snippet,
                    source_type=row.source_type,
                    reliability=row.reliability,
                    provider="baidu",
                    retrieved_at=row.retrieved_at,
                    provider_request_id=row.provider_request_id,
                    published_at=row.published_at,
                )
            sources = await repository.search_sources_for_run(run_id)

        service = ExperienceAtomService(session, embedding, distillation)
        candidates = await service.distill_run(run_id=run_id, goal_type="agent_app")
        if not candidates:
            raise RuntimeError("real distillation produced no reviewable candidate")
        approved = await service.approve(candidates[0].id)
        if approved.approved_atom_id is None:
            raise RuntimeError("candidate approval produced no atom")
        query_vector = (await embedding.embed([candidates[0].content]))[0]
        async with session_transaction(session):
            hits = await EvidenceRepository(session).rag_retrieve(
                goal_type="agent_app",
                vector=query_vector,
                limit=5,
                min_similarity=settings.rag_min_similarity,
            )
        print(
            json.dumps(
                {
                    "request_ids": request_ids,
                    "provider_result_count": len(raw_rows),
                    "persisted_source_count": len(sources),
                    "deduplicated_count": len(raw_rows) - len(sources),
                    "candidate_count": len(candidates),
                    "approved_atom_id": str(approved.approved_atom_id),
                    "embedding_provider": embedding.provider_name,
                    "rag_hit_count": len(hits),
                    "top_score": round(hits[0][1], 6) if hits else None,
                    "elapsed_ms": int((monotonic() - started) * 1000),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
