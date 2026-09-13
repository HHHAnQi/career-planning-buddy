"""Verify an approved real-chain ExperienceAtom can reach final Plan evidence refs."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.database import AsyncSessionFactory, session_transaction
from app.harness.snapshots import SnapshotService
from app.models.agent_run import AgentRun
from app.models.evidence import ExperienceAtom
from app.models.plan import Plan
from app.models.user import User
from app.models.user_profile import UserProfile
from app.providers.embedding import build_embedding_provider
from app.providers.evidence_distillation import build_evidence_distillation_provider
from app.providers.llm import MockPlanningProvider
from app.providers.search import build_search_provider
from app.tools.registry import build_tool_registry


async def main() -> None:
    settings = get_settings()
    embedding = build_embedding_provider(settings)
    await embedding.embed(["Stage 6B 本地向量模型预热"])
    distillation = build_evidence_distillation_provider(settings)
    search = build_search_provider(settings)
    async with AsyncSessionFactory() as session:
        async with session_transaction(session):
            atom = await session.scalar(
                select(ExperienceAtom)
                .where(ExperienceAtom.goal_type == "agent_app", ExperienceAtom.is_active.is_(True))
                .order_by(ExperienceAtom.created_at.desc())
                .limit(1)
            )
            if atom is None:
                raise RuntimeError("no approved ExperienceAtom available")
            user = User(guest_device_hash=sha256(uuid4().bytes).hexdigest())
            session.add(user)
            await session.flush()
            session.add(
                UserProfile(
                    user_id=user.id,
                    goal_type="agent_app",
                    stage="preparing",
                    time_budget_minutes=60,
                    skill_level="intermediate",
                    skill_summary="Python、FastAPI 与 Agent 应用基础",
                    preferences={},
                )
            )
            config = SnapshotService.build_config(settings)
            run = AgentRun(
                user_id=user.id,
                idempotency_key=f"stage6b-plan-{uuid4().hex[:16]}",
                request_text=(
                    "[mock:tool-rag] 请先检索已审核的 agent_app 知识，再生成一份两周求职计划，"
                    "并在 evidence_refs 中引用实际命中的 experience_atom。"
                ),
                hint_intent="create_plan",
                status="pending",
                graph_version=settings.agent_graph_version,
                config_snapshot_json=config.model_dump(mode="json"),
                deadline_at=datetime.now(UTC) + timedelta(seconds=180),
            )
            session.add(run)
            await session.flush()
            run_id = run.id
            atom_id = atom.id

    registry = build_tool_registry(
        settings=settings,
        session_factory=AsyncSessionFactory,
        embedding_provider=embedding,
        search_provider=search,
    )
    executor = AgentRunExecutor(
        AsyncSessionFactory,
        MockPlanningProvider(),
        registry,
        embedding,
        distillation,
    )
    await executor.execute(run_id)
    async with AsyncSessionFactory() as session:
        final_run = await session.get(AgentRun, run_id)
        plan = (
            await session.get(Plan, final_run.final_plan_id)
            if final_run and final_run.final_plan_id
            else None
        )
        refs = plan.evidence_refs_json if plan else []
        approved_refs = [ref for ref in refs if ref.get("kind") == "experience_atom"]
        target_refs = [ref for ref in approved_refs if str(ref.get("id")) == str(atom_id)]
        print(
            json.dumps(
                {
                    "run_status": final_run.status if final_run else "missing",
                    "result_kind": final_run.result_kind if final_run else None,
                    "error_code": final_run.error_code if final_run else None,
                    "fallback_reason": final_run.fallback_reason if final_run else None,
                    "final_plan_id": (
                        str(final_run.final_plan_id)
                        if final_run and final_run.final_plan_id
                        else None
                    ),
                    "evidence_ref_count": len(refs),
                    "approved_atom_ref_count": len(approved_refs),
                    "latest_atom_ref_count": len(target_refs),
                }
            )
        )
        if not approved_refs:
            raise RuntimeError("final Plan did not reference an approved ExperienceAtom")


if __name__ == "__main__":
    asyncio.run(main())
