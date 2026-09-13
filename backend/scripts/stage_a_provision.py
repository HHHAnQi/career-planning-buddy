"""Stage A pair-smoke provisioning (Option E′).

Creates TWO real Runtime ``EvalExperiment`` rows that share the same
case set, dataset, and trial_count, differing ONLY in
``Settings.eval_pair_smoke_planning_profile``:

  * ``stage-a-fixture-compact-v1``    → profile = compact_v1
  * ``stage-a-fixture-structured-v1`` → profile = structured_v1

Both runs are executed synchronously (no Agent-runtime background task)
through ``ExperimentRunner.run_experiment_and_grade``. Trial execution
itself never invokes a live LLM — fixture mode under
``eval_provider_mode=fixture`` wraps the deterministic Pair-Smoke
provider.

After this script returns, run ``scripts/stage_a_precheck.py`` to
verify the qualifying metrics (eligible ≥ 20, nonidentical ≥ 20,
comparison_signal_rate == 1.0).

Step #10 of the Option E′ acceptance sheet.
PR-9c.2 Commit 3.4 (Stage A).
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

# Stage5 cases whose expected result kind is ``plan`` (23 of them).
# These are the cases for which collect_evidence emits BOTH
# REQUEST_CONSTRAINTS and PLAN_PROJECTION; the other 7 either
# short-circuit to clarification/safe_response (no plan) or otherwise
# fail loader eligibility. Per reviewer Stage A E′ acceptance, the
# target population of eligible pairs is in [20, 30]; selecting 23
# plan-kind cases sits comfortably inside that band with no padding.
_PLAN_CASES: tuple[str, ...] = (
    "create-01", "create-02", "create-03", "create-04", "create-05",
    "create-06", "create-07", "create-08", "create-09", "create-10",
    "repair-01", "repair-02", "repair-03", "repair-04",
    "create-11", "create-12",
    "replan-01", "replan-02", "replan-03", "replan-04", "replan-05",
    "create-13", "create-14",
)
assert len(_PLAN_CASES) == 23


def _config(*, variant_role: str, baseline_experiment_id: UUID | None) -> ExperimentCreate:
    manifest = load_dataset().manifest
    return ExperimentCreate(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.source_sha256,
        git_commit="cced011",
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
    )


async def _seed_eval_trial_pair_rows(
    session: AsyncSession,
    *,
    baseline_trials: list[Any],
    candidate_trials: list[Any],
) -> int:
    """The experiment runner does NOT persist ``EvalTrialPair`` rows by
    itself — that's the dataset's responsibility. Seed them so
    ``scripts/export_pairwise_dataset.py`` (which only reads
    EvalTrialPair) can ship. We pair trials 1:1 by case_id.

    The ``pair_hash`` value persisted here is a placeholder identity
    used ONLY for the DB UNIQUE constraint — every downstream consumer
    (exporter → loader → Judge) recomputes the canonical ``pair_hash``
    from the frozen JSONL projections, so this placeholder never
    reaches the calibration pipeline. This keeps PairSmoke determinism
    from depending on a pre-computed hash landing in the DB."""

    from sqlalchemy import select

    baselines_by_case = {t.case_id: t for t in baseline_trials}
    candidates_by_case = {t.case_id: t for t in candidate_trials}
    created = 0
    repo = EvalRepository(session)
    for case_id in _PLAN_CASES:
        b = baselines_by_case.get(case_id)
        c = candidates_by_case.get(case_id)
        if b is None or c is None:
            continue
        # Skip if a prior provisioning run already created this pair.
        pre = await session.execute(
            select(EvalTrialPair).where(
                EvalTrialPair.baseline_trial_id == b.id,
                EvalTrialPair.candidate_trial_id == c.id,
            )
        )
        if pre.scalar_one_or_none() is not None:
            continue
        await repo.get_or_create_pair(
            EvalTrialPair(
                baseline_trial_id=b.id,
                candidate_trial_id=c.id,
                case_id=case_id,
                pair_hash=f"stage-a-placeholder:{b.id}:{c.id}",
                input_hash=f"stage-a-placeholder:{case_id}",
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
    factory = async_sessionmaker(
        bind=engine, expire_on_commit=False
    )
    try:
        # ---- baseline (compact_v1) ----------------------------------------
        baseline_settings = settings.model_copy(
            update={"eval_pair_smoke_planning_profile": "compact_v1"}
        )
        candidate_settings = settings.model_copy(
            update={"eval_pair_smoke_planning_profile": "structured_v1"}
        )
        loaded = load_dataset()
        filtered = filter_cases(loaded, list(_PLAN_CASES))

        async with factory() as session:
            service = EvalService(session)
            baseline_exp, baseline_trials = await service.create_experiment(
                dataset=filtered,
                config=_config(
                    variant_role="baseline",
                    baseline_experiment_id=None,
                ),
            )
            await session.commit()
        baseline_runner = ExperimentRunner(
            session_factory=factory, settings=baseline_settings
        )
        await baseline_runner.run_experiment_and_grade(
            baseline_exp.id, filtered, grade=True
        )

        # ---- candidate (structured_v1) -----------------------------------
        async with factory() as session:
            service = EvalService(session)
            candidate_exp, candidate_trials = await service.create_experiment(
                dataset=filtered,
                config=_config(
                    variant_role="candidate",
                    baseline_experiment_id=baseline_exp.id,
                ),
            )
            await session.commit()
        candidate_runner = ExperimentRunner(
            session_factory=factory, settings=candidate_settings
        )
        await candidate_runner.run_experiment_and_grade(
            candidate_exp.id, filtered, grade=True
        )

        # ---- seed EvalTrialPair rows -------------------------------------
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
            "baseline_label": "stage-a-fixture-compact-v1",
            "candidate_label": "stage-a-fixture-structured-v1",
            "plan_cases": list(_PLAN_CASES),
            "case_count": len(_PLAN_CASES),
            "seeded_trial_pair_rows": created_pairs,
        }
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _parse_args(argv)
    outcome = asyncio.run(provision())
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
