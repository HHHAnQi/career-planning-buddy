"""Stage B-1 provisioning — experiment-level agent variant.

Creates two real Runtime ``EvalExperiment`` rows that share the same
case set, dataset, and trial_count, differing ONLY in
``agent_variant``:

  * baseline   → ``compact_execution_v1``
  * candidate  → ``structured_reasoning_v1``

Both runs are executed synchronously through
``ExperimentRunner.run_experiment_and_grade``. The agent variant is
threaded through ``ExperimentRuntimeContext`` → ``TrialRunner`` →
``build_planning_provider(settings, agent_variant=...)`` — the new
Stage B-1a-lite injection path (NOT via global Settings).

This script does NOT depend on ``Settings.eval_pair_smoke_planning_profile``
(unlike ``stage_a_provision.py``). The variant identity lives on the
experiment row and flows through the runtime context.

PR-9c.2 Stage B-1a-lite E2E (Commit 3.5).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings  # noqa: E402
from app.models.eval import EvalTrialPair  # noqa: E402
from app.repositories.evals import EvalRepository  # noqa: E402
from app.services.evals import EvalService  # noqa: E402
from evals.v2.contracts import ExperimentCreate  # noqa: E402
from evals.v2.dataset_loader import filter_cases, load_dataset  # noqa: E402
from evals.v2.experiment_runner import ExperimentRunner  # noqa: E402

# Same 23 plan-kind stage5 cases as Stage A.
_PLAN_CASES: tuple[str, ...] = (
    "create-01", "create-02", "create-03", "create-04", "create-05",
    "create-06", "create-07", "create-08", "create-09", "create-10",
    "repair-01", "repair-02", "repair-03", "repair-04",
    "create-11", "create-12",
    "replan-01", "replan-02", "replan-03", "replan-04", "replan-05",
    "create-13", "create-14",
)
assert len(_PLAN_CASES) == 23


def _config(
    *,
    variant_role: str,
    baseline_experiment_id: UUID | None,
    agent_variant: str,
) -> ExperimentCreate:
    manifest = load_dataset().manifest
    return ExperimentCreate(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.source_sha256,
        git_commit="0f08a87",
        graph_version="stage5-v1",
        prompt_version="career-plan-v1",
        model_version="pair-smoke-v1",
        tool_version="tool-contract-v1",
        context_version="context-v1",
        memory_version="memory-v1",
        execution_mode="fixture_provider",
        variant_role=variant_role,
        baseline_experiment_id=baseline_experiment_id,
        trial_count=1,
        agent_variant=agent_variant,
    )


async def _seed_eval_trial_pair_rows(
    session: AsyncSession,
    *,
    baseline_trials: list[Any],
    candidate_trials: list[Any],
) -> int:
    """Seed ``EvalTrialPair`` rows by case_id (same as Stage A).
    Placeholder hashes are fine — downstream recompute from JSONL."""

    import hashlib

    baselines_by_case = {t.case_id: t for t in baseline_trials}
    candidates_by_case = {t.case_id: t for t in candidate_trials}
    created = 0
    repo = EvalRepository(session)
    for case_id in _PLAN_CASES:
        b = baselines_by_case.get(case_id)
        c = candidates_by_case.get(case_id)
        if b is None or c is None:
            continue
        pre = await session.execute(
            select(EvalTrialPair).where(
                EvalTrialPair.baseline_trial_id == b.id,
                EvalTrialPair.candidate_trial_id == c.id,
            )
        )
        if pre.scalar_one_or_none() is not None:
            continue
        ph = hashlib.sha256(f"b1:{b.id}:{c.id}".encode()).hexdigest()
        ih = hashlib.sha256(f"b1:{case_id}".encode()).hexdigest()
        await repo.get_or_create_pair(
            EvalTrialPair(
                baseline_trial_id=b.id,
                candidate_trial_id=c.id,
                case_id=case_id,
                pair_hash=ph,
                input_hash=ih,
                allowed_evidence_kinds=[
                    "REQUEST_CONSTRAINTS",
                    "PLAN_PROJECTION",
                ],
                judge_prompt_version="career-plan-v1",
                judge_rubric_version="rubric-v1",
            )
        )
        created += 1
    return created


async def provision(
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        loaded = load_dataset()
        filtered = filter_cases(loaded, list(_PLAN_CASES))

        # ---- baseline (compact_execution_v1) --------------------------
        async with factory() as session:
            service = EvalService(session)
            baseline_exp, baseline_trials = await service.create_experiment(
                dataset=filtered,
                config=_config(
                    variant_role="baseline",
                    baseline_experiment_id=None,
                    agent_variant="compact_execution_v1",
                ),
            )
            await session.commit()
        # NO settings.model_copy — agent_variant flows through the
        # experiment row → ExperimentRuntimeContext → TrialRunner.
        baseline_runner = ExperimentRunner(
            session_factory=factory, settings=settings
        )
        await baseline_runner.run_experiment_and_grade(
            baseline_exp.id, filtered, grade=True
        )

        # ---- candidate (structured_reasoning_v1) ----------------------
        async with factory() as session:
            service = EvalService(session)
            candidate_exp, candidate_trials = await service.create_experiment(
                dataset=filtered,
                config=_config(
                    variant_role="candidate",
                    baseline_experiment_id=baseline_exp.id,
                    agent_variant="structured_reasoning_v1",
                ),
            )
            await session.commit()
        candidate_runner = ExperimentRunner(
            session_factory=factory, settings=settings
        )
        await candidate_runner.run_experiment_and_grade(
            candidate_exp.id, filtered, grade=True
        )

        # ---- seed EvalTrialPair rows ----------------------------------
        async with factory() as session:
            created_pairs = await _seed_eval_trial_pair_rows(
                session,
                baseline_trials=baseline_trials,
                candidate_trials=candidate_trials,
            )
            await session.commit()

        return {
            "ok": True,
            "baseline_experiment_id": str(baseline_exp.id),
            "candidate_experiment_id": str(candidate_exp.id),
            "baseline_agent_variant": "compact_execution_v1",
            "candidate_agent_variant": "structured_reasoning_v1",
            "baseline_label": "stage-b1-compact-execution-v1",
            "candidate_label": "stage-b1-structured-reasoning-v1",
            "plan_cases": list(_PLAN_CASES),
            "case_count": len(_PLAN_CASES),
            "seeded_trial_pair_rows": created_pairs,
        }
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return argparse.ArgumentParser(description=__doc__).parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _parse_args(argv)
    outcome = asyncio.run(provision())
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
