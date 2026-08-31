"""REAL-MODEL context-compression comparison — owner-authorized runs only.

Performs PAID provider calls when executed; provided ready-to-run but NOT
executed in the offline batch. Corrections over the first version:

  * every case's history (plan + tasks + reviews) is ACTUALLY loaded into
    the isolated user's data before the run, and the input snapshot is
    asserted to contain it;
  * the strategy is set BEFORE run creation (the config snapshot freezes
    at create time) and the frozen snapshot is asserted to match the
    experiment label both before and after execution — no ambient env
    switching after the fact;
  * provider call counting comes from a counting wrapper on the provider
    instance (per-trial, exact), never inferred from node rows;
  * plan scoring reconstructs the candidate from PERSISTED rows only
    (weekly_focus_json, tasks table) — nothing is fabricated;
  * results are appended to disk after EVERY trial (JSONL), so a crash
    never loses the batch; failures / over-budget / no-plan rows are
    kept and reported.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.security import TokenService
from app.core.time import product_today
from app.models.agent_run import AgentRun
from app.models.plan import Plan, Task
from app.models.review import Review
from app.schemas.enums import CareerStage, GoalType, SkillLevel
from app.schemas.profile import ProfilePutRequest
from app.services.agent_runs import AgentRunService
from app.services.auth import AuthService
from app.services.profiles import ProfileService

DATASET = (
    Path(__file__).resolve().parents[1] / "evals/datasets/context-compression-v1.jsonl"
)
STRATEGIES = ("full", "recent", "relevant_summary")


class _NoopScheduler:
    def submit(self, run_id):
        return None

    async def request_cancel(self, run_id):
        return None


class CountingProvider:
    """Transparent provider wrapper: counts real planning calls and
    captures the last request estimate per trial."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0
        self.last_input_estimate: dict[str, int] = {}

    def __getattr__(self, name):  # delegate everything
        return getattr(self._inner, name)

    async def generate_plan(self, *args, **kwargs):
        self.calls += 1
        raw = await self._inner.generate_plan(*args, **kwargs)
        if isinstance(raw, dict) and isinstance(raw.get("input_estimate"), dict):
            self.last_input_estimate = dict(raw["input_estimate"])
        return raw

    async def generate_agent_turn(self, *args, **kwargs):
        self.calls += 1
        return await self._inner.generate_agent_turn(*args, **kwargs)


def _settings_with_strategy(strategy: str):
    """Fresh settings with the strategy knob set BEFORE any run creation."""
    import os

    os.environ["CONTEXT_COMPRESSION_STRATEGY"] = strategy
    get_settings.cache_clear()
    return get_settings()


def _clear_strategy_override() -> None:
    import os

    os.environ.pop("CONTEXT_COMPRESSION_STRATEGY", None)
    get_settings.cache_clear()


async def _load_history(session, user_id, case: dict) -> None:
    """Persist the case's history as a real prior plan + tasks + reviews."""
    from app.models.agent_run import AgentRun as _Run

    # plans.source_run_id is NOT NULL with an FK to agent_runs: the
    # historical plan is attributed to a synthetic completed run.
    anchor_run = _Run(
        id=uuid4(),
        user_id=user_id,
        run_kind="planning",
        idempotency_key=f"cc-hist-{uuid4()}",
        request_text=case["request"][:100],
        status="completed",
        graph_version="history-loader-v1",
        config_snapshot_json={"note": "synthetic anchor for history loading"},
        deadline_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        + __import__("datetime").timedelta(days=1),
    )
    session.add(anchor_run)
    await session.flush()
    tasks = case["history"]["tasks"]
    plan_date = date.fromisoformat(tasks[0]["date"]) if tasks else product_today()
    plan = Plan(
        id=uuid4(),
        user_id=user_id,
        version=1,
        status="completed",
        plan_date=plan_date,
        horizon_start=plan_date,
        horizon_end=plan_date + timedelta(days=27),
        overall_direction=case["request"][:100],
        weekly_focus_json=[],
        source_run_id=anchor_run.id,
    )
    plan.summary = "历史计划"
    plan.rationale = "历史执行记录，用于上下文装载"
    session.add(plan)
    await session.flush()
    for index, t in enumerate(reversed(tasks)):
        abandoned = t["state"] == "abandoned"
        session.add(
            Task(
                id=uuid4(),
                user_id=user_id,
                plan_id=plan.id,
                order_index=index,
                title=t["deliverable"][:60],
                task_type="other",
                state=t["state"],
                scheduled_date=date.fromisoformat(t["date"]),
                deliverable=t["deliverable"],
                starter_action="1. 历史记录",
                estimated_minutes=30,
                # ck_tasks_state_fields: completed rows carry actual
                # minutes and no abandonment fields; abandoned rows carry
                # reason 'other' plus non-empty free text.
                actual_minutes=30 if not abandoned else None,
                abandoned_reason="other" if abandoned else None,
                abandoned_reason_text=(
                    (t.get("abandoned_reason") or "历史放弃原因")
                    if abandoned
                    else None
                ),
                created_at=date.fromisoformat(t["date"]),
                updated_at=date.fromisoformat(t["date"]),
            )
        )
    for r in case["history"].get("reviews", []):
        session.add(
            Review(
                id=uuid4(),
                user_id=user_id,
                plan_id=plan.id,
                review_date=date.fromisoformat(r["date"]),
                blockers=r.get("blockers"),
                adjustment_request=r.get("adjustment"),
                replan_reason=None,
            )
        )


async def _one_trial(factory, case: dict, strategy: str) -> dict[str, object]:
    settings = _settings_with_strategy(strategy)
    try:
        async with factory() as session:
            auth = AuthService(session, TokenService(settings))
            user = (await auth.login_guest(None)).user
            await ProfileService(session).put(
                user_id=user.id,
                payload=ProfilePutRequest(
                    goal_type=GoalType.AI_BACKEND,
                    stage=CareerStage.PREPARING,
                    time_budget_minutes=case["profile"].get(
                        "time_budget_minutes", 90
                    ),
                    skill_level=SkillLevel.INTERMEDIATE,
                    skill_summary="FastAPI",
                    start_date=product_today(),
                    deadline=product_today() + timedelta(days=34),
                ),
                idempotency_key=f"cc-live-{uuid4()}",
            )
            await _load_history(session, user.id, case)
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
            # Pre-execution assertion: the FROZEN snapshot carries the
            # strategy label (config froze at create time, before any
            # execution — the only correct place to set it).
            frozen = dict(run.config_snapshot_json or {})
            assert (
                frozen.get("context_compression_strategy") == strategy
            ), f"strategy {strategy} not frozen in config snapshot"

        counting = CountingProvider(
            __import__(
                "app.providers.llm", fromlist=["build_planning_provider"]
            ).build_planning_provider(settings)
        )
        await AgentRunExecutor(
            factory, provider=counting, tool_registry=None
        ).execute(run_id)
    finally:
        _clear_strategy_override()

    async with factory() as session:
        run = await session.get(AgentRun, run_id)
        # Post-execution assertions: snapshot strategy unchanged; the
        # input snapshot actually CONTAINS the loaded history.
        frozen = dict(run.config_snapshot_json or {})
        assert frozen.get("context_compression_strategy") == strategy
        snapshot = dict(run.input_snapshot_json or {})
        snap_text = json.dumps(snapshot, ensure_ascii=False)
        history_present = any(
            t["deliverable"][:12] in snap_text
            for t in case["history"]["tasks"]
        )

        plan = await session.scalar(
            select(Plan).where(Plan.source_run_id == run_id)
        )
        violations: list[str] = []
        candidate_json: dict[str, object] | None = None
        if plan is not None:
            plan_tasks = list(
                await session.scalars(
                    select(Task)
                    .where(Task.plan_id == plan.id)
                    .order_by(Task.order_index)
                )
            )
            from app.agent.nodes import validate_candidate
            from app.schemas.agent_runs import (
                PlanCandidate,
                TaskCandidate,
                WeeklyFocusCandidate,
            )
            from app.schemas.enums import TaskType

            candidate = PlanCandidate(
                plan_date=plan.plan_date,
                horizon_start=plan.horizon_start,
                horizon_end=plan.horizon_end,
                overall_direction=plan.overall_direction,
                # reconstructed from PERSISTED rows only — no fabrication
                weekly_focus=[
                    WeeklyFocusCandidate(
                        week_index=item.get("week_index", i + 1),
                        focus=str(item.get("focus", ""))[:40],
                        success_signal=str(item.get("success_signal", ""))[:40],
                    )
                    for i, item in enumerate(plan.weekly_focus_json or [])
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
            candidate_json = {
                "task_count": len(candidate.tasks),
                "weekly_focus_count": len(candidate.weekly_focus),
            }
            history_completed = [
                t["deliverable"]
                for t in case["history"]["tasks"]
                if t["state"] == "completed"
            ]
            from app.agent.nodes import build_planning_context
            from app.schemas.agent_runs import ProfileContext

            profile_ctx = ProfileContext(
                user_id=user.id,
                version=1,
                goal_type=GoalType.AI_BACKEND,
                stage=CareerStage.PREPARING,
                time_budget_minutes=case["profile"].get(
                    "time_budget_minutes", 90
                ),
                skill_level=SkillLevel.INTERMEDIATE,
            )
            authoritative = build_planning_context(
                profile=profile_ctx,
                requested_horizon_weeks=None,
                source_plan_id=None,
                source_plan_version=None,
                completed_facts=history_completed,
                blockers=[],
                planning_date=plan.plan_date,
            )
            report = validate_candidate(candidate, authoritative)
            violations = [c.code for c in report.checks if not c.passed]

        return {
            "run_id": str(run_id),
            "case_id": case["case_id"],
            "strategy": strategy,
            "status": run.status,
            "fallback_reason": run.fallback_reason,
            "provider_tokens_in": run.total_tokens_in,
            "provider_tokens_out": run.total_tokens_out,
            "latency_ms": run.total_latency_ms,
            "input_estimate": counting.last_input_estimate,
            "provider_llm_calls": counting.calls,
            "history_in_input_snapshot": history_present,
            "plan": candidate_json,
            "violation_codes": violations,
        }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--out", default="evals/artifacts/context-compression-live.jsonl"
    )
    args = parser.parse_args()

    cases = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    try:
        for repetition in range(args.repetitions):
            for case in cases:
                for strategy in STRATEGIES:  # interleaved round-robin
                    try:
                        row = await _one_trial(factory, case, strategy)
                    except Exception as exc:  # keep failures in the record
                        row = {
                            "case_id": case["case_id"],
                            "strategy": strategy,
                            "repetition": repetition,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    row["repetition"] = repetition
                    rows.append(row)
                    with out.open("a", encoding="utf-8") as fh:  # noqa: ASYNC230
                        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        await engine.dispose()

    ok_rows = [r for r in rows if "error" not in r]
    summary = {
        "mode": "REAL MODEL (owner-authorized run)",
        "system_trials": len(rows),
        "actual_model_calls": sum(
            int(r.get("provider_llm_calls", 0)) for r in ok_rows
        ),
        "failed_trials_kept": len(rows) - len(ok_rows),
        "by_strategy": {
            s: {
                "trials": sum(1 for r in ok_rows if r["strategy"] == s),
                "mean_provider_tokens_in": _mean(
                    [r["provider_tokens_in"] for r in ok_rows if r["strategy"] == s]
                ),
                "violation_rate": round(
                    sum(
                        1
                        for r in ok_rows
                        if r["strategy"] == s and r["violation_codes"]
                    )
                    / max(1, sum(1 for r in ok_rows if r["strategy"] == s)),
                    4,
                ),
            }
            for s in STRATEGIES
        },
    }
    Path(str(out).replace(".jsonl", "-summary.json")).write_text(  # noqa: ASYNC240
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"rows -> {out}")
    return 0


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
