"""Command-line entrypoint for the Eval Harness V2.

Invoke as ``python -m evals.v2 run``. The CLI creates (or resumes) an
Experiment, drives its Trials through the real Runtime via
``ExperimentRunner.run_experiment_and_grade``, and prints a single JSON
report line on stdout. Exit codes::

    0   report emitted and requested quality gates passed
    1   execution failed (error envelope printed)
    2   configuration / argument error
    3   report emitted but the requested quality gate failed

The HTTP ``/api/v1/eval/runs`` endpoint is intentionally out of scope --
this entrypoint exists for batch driver runs and local development.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.runtime.versioning import build_runtime_identity
from app.services.evals import EvalService
from evals.v2.bad_case_export import write_bad_cases
from evals.v2.contracts import ExperimentCreate
from evals.v2.dataset_loader import DatasetBundle, filter_cases, load_dataset
from evals.v2.experiment_runner import ExperimentRunner
from evals.v2.runtime_smoke import load_runtime_smoke_dataset

_PROVIDER_MODE_TO_EXECUTION: dict[str, str] = {
    "mock": "mock_provider",
    "fixture": "fixture_provider",
    "live": "live_provider",
}


def _build_dataset(args: argparse.Namespace) -> DatasetBundle:
    if args.dataset == "stage5":
        bundle = load_dataset()
    else:
        bundle = load_runtime_smoke_dataset()
    mode = args.provider_mode
    if mode is None:
        from app.core.config import get_settings

        mode = get_settings().eval_provider_mode
    # Mode-scoped validity: [mock:tool-...] cases script TOOL CALLS for the
    # deterministic mock — a real model gets no callable signal from them.
    # (Other [mock:...] markers, e.g. malformed-output scripts for repair
    # cases, are live-tolerable: the case passes whenever the model simply
    # does not err.) live_only cases (seeded memories + natural references)
    # only make sense against a real model.
    if mode == "live":
        bundle.cases = [
            case
            for case in bundle.cases
            if "[mock:tool-" not in case.scenario.user_request
        ]
    else:
        bundle.cases = [
            case for case in bundle.cases if not case.scenario.confirmed_memories
        ]
    if args.cases:
        bundle = filter_cases(bundle, list(args.cases))
    return bundle


def _build_config(
    settings: Settings, bundle: DatasetBundle, *, trial_count: int
) -> ExperimentCreate:
    manifest = bundle.manifest
    identity = build_runtime_identity(settings)
    return ExperimentCreate(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.source_sha256,
        git_commit=identity.git_commit,
        graph_version=identity.graph_version,
        feature_stage=identity.feature_stage,
        prompt_version=identity.primary_prompt_version,
        model_version=settings.llm_model or "mock-v1",
        tool_version=identity.tool_contract_version,
        context_version=identity.context_version,
        memory_version=identity.memory_version,
        search_version=identity.search_version,
        eval_harness_version=identity.eval_harness_version,
        execution_mode=_PROVIDER_MODE_TO_EXECUTION[settings.eval_provider_mode],
        variant_role="baseline",
        trial_count=trial_count,
    )


def _all_hard_gates_passed(report: Mapping[str, object]) -> bool:
    """Return whether every requested Trial completed, was scored, and passed."""

    trial_count = report.get("trial_count")
    completed_count = report.get("completed_trial_count")
    scored_count = report.get("scored_trial_count")
    pass_fraction = report.get("hard_gate_pass_fraction")
    return (
        isinstance(trial_count, int)
        and trial_count > 0
        and completed_count == trial_count
        and scored_count == trial_count
        and isinstance(pass_fraction, (int, float))
        and float(pass_fraction) >= 1.0
    )


async def _run_experiment(args: argparse.Namespace, settings: Settings) -> dict[str, object]:
    dataset = _build_dataset(args)
    engine = create_async_engine(settings.database_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            if args.experiment_id:
                experiment_id = UUID(args.experiment_id)
            else:
                config = _build_config(settings, dataset, trial_count=args.trial_count)
                experiment, _ = await EvalService(session).create_experiment(
                    dataset=dataset,
                    config=config,
                    run_type=args.run_type,
                    fixture_source_experiment_id=(
                        UUID(args.fixture_source_experiment_id)
                        if args.fixture_source_experiment_id
                        else None
                    ),
                )
                experiment_id = experiment.id
                await session.commit()

        runner = ExperimentRunner(session_factory=session_factory, settings=settings)
        report = await runner.run_experiment_and_grade(
            experiment_id, dataset, grade=not args.no_grade
        )
        write_bad_cases(report)
        return report.to_dict()
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.v2",
        description="Eval Harness V2 batch driver.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="Run an eval experiment end-to-end.")
    run_p.add_argument(
        "--dataset",
        choices=["stage5", "runtime-smoke"],
        default="stage5",
        help="Dataset to load (default: stage5).",
    )
    run_p.add_argument(
        "--cases",
        nargs="*",
        default=None,
        help="Subset of case_ids to keep (default: all cases in the dataset).",
    )
    run_p.add_argument(
        "--provider-mode",
        choices=["mock", "fixture", "live"],
        default=None,
        help="Override settings.eval_provider_mode for this run.",
    )
    run_p.add_argument(
        "--trial-count",
        type=int,
        default=1,
        help="Number of Trials per case (>=1).",
    )
    run_p.add_argument(
        "--run-type",
        choices=["evaluation", "fixture_replay"],
        default="evaluation",
        help="Execution kind (fixture_replay requires a frozen source Experiment).",
    )
    run_p.add_argument(
        "--fixture-source-experiment-id",
        type=str,
        default=None,
        help="Completed fixture-provider Experiment used by fixture_replay.",
    )
    run_p.add_argument(
        "--no-grade",
        action="store_true",
        help="Skip the grading phase after executing Trials.",
    )
    run_p.add_argument(
        "--experiment-id",
        type=str,
        default=None,
        help="Resume an existing Experiment instead of creating a new one.",
    )
    run_p.add_argument(
        "--require-all-hard-gates",
        action="store_true",
        help=(
            "Exit 3 unless every requested Trial completes, is scored, and passes "
            "all hard gates."
        ),
    )
    args = parser.parse_args()

    if args.cmd == "run":
        try:
            settings = get_settings()
        except ValidationError as exc:
            print(
                json.dumps(
                    {
                        "status": "configuration_error",
                        "error_type": type(exc).__name__,
                        "details": exc.errors(),
                    }
                )
            )
            return 2

        if args.provider_mode is not None:
            settings = settings.model_copy(
                update={"eval_provider_mode": args.provider_mode}
            )
    else:
        return 2

    try:
        result = asyncio.run(_run_experiment(args, settings))
    except Exception as exc:  # noqa: BLE001 -- CLI boundary must print JSON.
        payload: dict[str, object] = {
            "status": "failed",
            "error_code": getattr(exc, "code", "EVAL_RUN_FAILED"),
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 1

    result["checked_at"] = datetime.now(UTC).isoformat()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    if args.require_all_hard_gates and not _all_hard_gates_passed(result):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
