"""REAL-MODEL context-compression comparison — owner-authorized runs only.

NOTE: this script performs PAID provider calls (GLM-4.7 via the configured
LLM_* env). It is provided ready-to-run but deliberately NOT executed in
the offline batch. Offline evidence lives in
evals/artifacts/context-compression-v1-report.json.

Design (mandate #7):
- isolated data: each (case, strategy, repetition) gets its own guest user
  and corpus, so no cross-run contamination;
- interleaved: strategies are round-robin interleaved per repetition to
  spread provider drift evenly across arms;
- repeated measures: --repetitions N (default 3);
- failures and over-budget results are KEPT and reported, never dropped;
- reports system trials and actual model calls separately (trials may
  make 1-3 calls each: plan + bounded repair).

Metrics (frozen, see docs/standards/metric-registry.md):
- input sizes: provider-reported usage.tokens_in (actual) PLUS the
  estimator fields recorded by the graph telemetry (estimate_*);
- plan_constraint_violation_rate: validate_candidate(candidate,
  authoritative_context) failing checks, per case.

Usage (requires explicit authorization):
  cd backend
  python scripts/context_compression_live.py --repetitions 3 \
      --out evals/artifacts/context-compression-live.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.executor import AgentRunExecutor
from app.agent.nodes import validate_candidate
from app.core.config import get_settings
from app.core.security import TokenService
from app.core.time import product_today
from app.models.agent_run import AgentRun, AgentStep
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePutRequest
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.profiles import ProfileService

DATASET = Path(__file__).resolve().parents[1] / "evals/datasets/context-compression-v1.jsonl"
STRATEGIES = ("full", "recent", "relevant_summary")


class _NoopScheduler:
    def submit(self, run_id):
        return None

    async def request_cancel(self, run_id):
        return None


async def _one_trial(factory, case: dict, strategy: str) -> dict[str, object]:
    settings = get_settings()
    async with factory() as session:
        auth = AuthService(session, TokenService(settings))
        user = (await auth.login_guest(None)).user
        await ProfileService(session).put(
            user_id=user.id,
            payload=ProfilePutRequest(
                goal_type=GoalType.AI_BACKEND,
                stage=CareerStage.PREPARING,
                time_budget_minutes=case["profile"].get("time_budget_minutes", 90),
                skill_level=SkillLevel.INTERMEDIATE,
                skill_summary="FastAPI",
                start_date=product_today(),
                deadline=product_today() + timedelta(days=34),
            ),
            idempotency_key=f"cc-live-{uuid4()}",
        )
        service = AgentRunService(session, settings, _NoopScheduler())
        run = await service.create(
            user_id=user.id,
            message=case["request"],
            hint_intent="create_plan",
            goal_type_override=None,
            source_plan_id=None,
            idempotency_key=f"cc-live-{uuid4()}",
        )
        await session.commit()
        run_id = run.id

    # Strategy is injected per-executor via the settings override so the
    # frozen config snapshot records it (auditable arm identity).
    import os

    os.environ["CONTEXT_COMPRESSION_STRATEGY"] = strategy
    get_settings.cache_clear()
    await AgentRunExecutor(factory).execute(run_id)
    os.environ.pop("CONTEXT_COMPRESSION_STRATEGY", None)
    get_settings.cache_clear()

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        steps = list(
            await session.scalars(
                select(AgentStep).where(
                    AgentStep.run_id == run_id,
                    AgentStep.node_name == "career_planning_agent",
                )
            )
        )
        estimates = [
            s.trace_data.get("input_estimate_total_tokens")
            for s in steps
            if isinstance(s.trace_data, dict)
        ]
        provider_calls = sum(
            1
            for s in steps
            if isinstance(s.trace_data, dict) and s.trace_data.get("tokens_in")
        )
        from app.models.plan import Plan, Task

        plan = await session.scalar(
            select(Plan).where(Plan.source_run_id == run_id)
        )
        violation_codes: list[str] = []
        if plan is not None:
            from app.schemas.agent_runs import (
                PlanCandidate,
                TaskCandidate,
                WeeklyFocusCandidate,
            )
            from app.schemas.enums import TaskType

            plan_tasks = list(
                await session.scalars(
                    select(Task).where(Task.plan_id == plan.id).order_by(Task.order_index)
                )
            )
            candidate = PlanCandidate(
                plan_date=plan.plan_date,
                horizon_start=plan.horizon_start,
                horizon_end=plan.horizon_end,
                overall_direction=plan.overall_direction,
                weekly_focus=[
                    WeeklyFocusCandidate(
                        week_index=i + 1,
                        focus=(plan.overall_direction or "goal")[:40],
                        success_signal=(plan.overall_direction or "goal")[:40],
                    )
                    for i in range(
                        max(1, (plan.horizon_end - plan.horizon_start).days // 7)
                    )
                ],
                summary=plan.summary or "",
                rationale=plan.rationale or "",
                tasks=[
                    TaskCandidate(
                        title=t.title,
                        task_type=TaskType(t.task_type),
                        scheduled_date=t.scheduled_date,
                        starter_action=t.starter_action or "",
                        deliverable=t.deliverable or "",
                        estimated_minutes=t.estimated_minutes,
                        rationale=t.rationale or "",
                    )
                    for t in plan_tasks
                ],
            )
            # Authoritative (pre-compression) facts: completed deliverables
            # from the case history — the same set the runtime validator
            # consumed. Violations are computed against THESE, never the
            # compressed input.
            history_completed = [
                t["deliverable"]
                for t in case["history"]["tasks"]
                if t["state"] == "completed"
            ]
            from uuid import uuid4 as _u

            from app.agent.nodes import build_planning_context
            from app.schemas.agent_runs import ProfileContext

            profile = ProfileContext(
                user_id=_u(),
                version=1,
                goal_type=GoalType.AI_BACKEND,
                stage=CareerStage.PREPARING,
                time_budget_minutes=case["profile"].get("time_budget_minutes", 90),
                skill_level=SkillLevel.INTERMEDIATE,
            )
            authoritative = build_planning_context(
                profile=profile,
                requested_horizon_weeks=None,
                source_plan_id=None,
                source_plan_version=None,
                completed_facts=history_completed,
                blockers=[],
                planning_date=plan.plan_date,
            )
            report = validate_candidate(candidate, authoritative)
            violation_codes = [
                c.code for c in report.checks if not c.passed
            ]
        return {
            "run_id": str(run_id),
            "strategy": strategy,
            "status": run.status,
            "fallback_reason": run.fallback_reason,
            "provider_tokens_in": run.total_tokens_in,
            "provider_tokens_out": run.total_tokens_out,
            "latency_ms": run.total_latency_ms,
            "estimate_tokens": estimates,
            "provider_llm_calls": provider_calls,
            "violation_codes": violation_codes,
        }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--out", default="evals/artifacts/context-compression-live.json"
    )
    args = parser.parse_args()

    cases = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    rows: list[dict[str, object]] = []
    try:
        for repetition in range(args.repetitions):
            for case in cases:
                # Interleaved round-robin across strategies.
                for strategy in STRATEGIES:
                    row = await _one_trial(factory, case, strategy)
                    row["case_id"] = case["case_id"]
                    row["repetition"] = repetition
                    rows.append(row)
    finally:
        await engine.dispose()

    trials = len(rows)
    calls = sum(int(r["provider_llm_calls"]) for r in rows)
    summary = {
        "mode": "REAL MODEL (owner-authorized run)",
        "system_trials": trials,
        "actual_model_calls": calls,
        "failures_kept": [r for r in rows if r["status"] != "completed"],
        "by_strategy": {
            s: {
                "mean_provider_tokens_in": _mean(
                    [r["provider_tokens_in"] for r in rows if r["strategy"] == s]
                ),
                "mean_latency_ms": _mean(
                    [r["latency_ms"] for r in rows if r["strategy"] == s]
                ),
            }
            for s in STRATEGIES
        },
    }
    payload_text = json.dumps(
        {"summary": summary, "rows": rows}, ensure_ascii=False, indent=2
    )
    Path(args.out).write_text(  # noqa: ASYNC240
        payload_text,
        encoding="utf-8",
    )
    print(json.dumps(summary["by_strategy"], ensure_ascii=False, indent=2))
    print(f"trials={trials} model_calls={calls} -> {args.out}")
    return 0


def _mean(values: list[int]) -> float:
    return round(sum(values) / len(values), 1) if values else None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
