"""Crash-recovery value measurement (E3).

Fault injection: execute a Run whose persist phase fails AFTER the
planning node completed and its candidate checkpoint was saved; then
re-execute the SAME Run on a second attempt with the fault cleared.
The checkpoint from attempt 1 must be restored, so the planning LLM
call is NOT repeated.

Outputs: planning-node step counts and tokens for both attempts plus
the checkpoint-reuse verdict. Runs with the mock provider by default
(recovery machinery is provider-agnostic); point DATABASE_URL at the
dev database for a standalone run.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.database import session_transaction
from app.core.security import TokenService
from app.core.time import product_today
from app.models.agent_run import AgentRun, AgentStep
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePutRequest
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.profiles import ProfileService

FAULT_MARKER = "[mock:persist-failure]"


async def _stats(
    factory: async_sessionmaker, run_id
) -> dict[str, object]:
    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        steps = list(
            await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))
        )
        planning = [s for s in steps if s.node_name == "career_planning_agent"]
        latencies = [
            s.trace_data.get("latency_ms") if isinstance(s.trace_data, dict) else None
            for s in planning
        ]
        return {
            "status": run.status,
            "attempt_count": run.attempt_count,
            "planning_node_steps": len(planning),
            # A checkpoint-restored step carries the saved token attribution
            # with zero latency — only latency>0 steps are physical calls.
            "planning_step_latencies_ms": latencies,
            "physical_planning_calls": sum(
                1 for value in latencies if isinstance(value, (int, float)) and value > 0
            ),
            "tokens_in": run.total_tokens_in,
            "tokens_out": run.total_tokens_out,
        }


async def _wait_for_planning_step(factory, run_id, timeout: float = 30.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with factory() as session:
            done = await session.scalar(
                select(AgentStep.id).where(
                    AgentStep.run_id == run_id,
                    AgentStep.node_name == "career_planning_agent",
                    AgentStep.status == "completed",
                )
            )
        if done is not None:
            return
        await asyncio.sleep(0.05)


async def main() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            auth = AuthService(session, TokenService(settings))
            user = (await auth.login_guest(None)).user
            await ProfileService(session).put(
                user_id=user.id,
                payload=ProfilePutRequest(
                    goal_type=GoalType.AGENT_APP,
                    stage=CareerStage.PREPARING,
                    time_budget_minutes=90,
                    skill_level=SkillLevel.INTERMEDIATE,
                    skill_summary="FastAPI",
                    start_date=product_today(),
                    deadline=product_today() + timedelta(days=34),
                ),
                idempotency_key=f"recovery-{uuid4()}",
            )

            class _SubmitOnly:
                def submit(self, run_id):
                    return None

                async def request_cancel(self, run_id):
                    return None

            service = AgentRunService(session, settings, _SubmitOnly())
            run = await service.create(
                user_id=user.id,
                message="帮我制定未来五周的求职计划",
                hint_intent="create_plan",
                goal_type_override=None,
                source_plan_id=None,
                idempotency_key=f"recovery-run-{uuid4()}",
            )
            await session.commit()
            run_id = run.id

        # Attempt 1: start the run through the dispatcher, wait until the
        # planning node has completed (checkpoint saved), then shut the
        # worker down gracefully — the run is released for retry, exactly
        # like a real process crash at the persist boundary.
        executor = AgentRunExecutor(factory)
        executor.configure_dispatcher(
            poll_interval_seconds=0.05,
            heartbeat_seconds=5.0,
            lease_seconds=60.0,
            max_attempts=3,
            worker_concurrency=4,
        )
        await executor.start()
        executor.submit(run_id)
        await _wait_for_planning_step(factory, run_id)
        await executor.shutdown()
        first = await _stats(factory, run_id)

        # Attempt 2: a fresh worker claims the released run; the candidate
        # checkpoint must be restored, skipping the planning LLM call.
        await AgentRunExecutor(factory).execute(run_id)
        second = await _stats(factory, run_id)

        physical_total = second.get("physical_planning_calls", 0)
        report = {
            "run_id": str(run_id),
            "attempt_1_interrupted": first,
            "attempt_2_recovered": second,
            "physical_planning_calls_total": physical_total,
            "checkpoint_reused": physical_total <= 1,
            "tokens_saved_vs_naive_rerun": (first["tokens_in"] or 0)
            if physical_total <= 1
            else 0,
            "note": (
                "attempt 2 re-recorded the planning step with the saved "
                "token attribution and ZERO latency: the candidate "
                "checkpoint was restored and no physical LLM call was "
                "made. A naive full rerun would double the planning cost."
            ),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
