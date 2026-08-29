"""Run a small live Direct-LLM (B0) versus Full-Agent (B3) Eval.

The two experiments share the same dataset, model, versions, and trial count.
Only ``agent_variant`` changes. Each completed pair is then judged twice by
the independently configured Judge model with baseline/swapped presentation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.agent.errors import ProviderConfigurationError  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.repositories.evals import EvalRepository  # noqa: E402
from app.services.evals import EvalService  # noqa: E402
from evals.v2.contracts import ExperimentCreate  # noqa: E402
from evals.v2.dataset_loader import filter_cases, load_dataset  # noqa: E402
from evals.v2.experiment_runner import ExperimentRunner  # noqa: E402
from evals.v2.judge_factory import build_pairwise_judge  # noqa: E402
from evals.v2.pairwise import PositionVariant  # noqa: E402

_DEFAULT_CASES = ("create-01", "replan-01")


def _validate_live_settings(settings: Settings) -> None:
    if settings.eval_provider_mode != "live":
        raise ProviderConfigurationError("EVAL_PROVIDER_MODE must be live")
    if settings.llm_provider != "openai_compatible":
        raise ProviderConfigurationError("LLM_PROVIDER must be openai_compatible")
    if settings.judge_llm_provider != "openai_compatible":
        raise ProviderConfigurationError(
            "JUDGE_LLM_PROVIDER must be openai_compatible"
        )


def _config(
    *,
    settings: Settings,
    dataset_hash: str,
    dataset_id: str,
    dataset_version: str,
    git_commit: str,
    variant_role: str,
    baseline_experiment_id: UUID | None,
    agent_variant: str,
) -> ExperimentCreate:
    return ExperimentCreate(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_hash=dataset_hash,
        git_commit=git_commit,
        graph_version="stage6-live-v1",
        prompt_version="b0-b3-comparison-v1",
        model_version=settings.llm_model or "unconfigured",
        tool_version="tool-contract-v1",
        context_version="context-v1",
        memory_version="memory-v1",
        execution_mode="live_provider",
        variant_role=variant_role,
        baseline_experiment_id=baseline_experiment_id,
        trial_count=1,
        agent_variant=agent_variant,
    )


async def run_smoke(
    *,
    cases: list[str],
    git_commit: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    _validate_live_settings(settings)
    loaded = load_dataset()
    dataset = filter_cases(loaded, cases)
    manifest = dataset.manifest
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    try:
        async with factory() as session:
            baseline, _ = await EvalService(session).create_experiment(
                dataset=dataset,
                config=_config(
                    settings=settings,
                    dataset_hash=manifest.source_sha256,
                    dataset_id=manifest.dataset_id,
                    dataset_version=manifest.dataset_version,
                    git_commit=git_commit,
                    variant_role="baseline",
                    baseline_experiment_id=None,
                    agent_variant="direct_llm_v1",
                ),
            )
            await session.commit()

        baseline_report = await ExperimentRunner(
            session_factory=factory, settings=settings
        ).run_experiment_and_grade(baseline.id, dataset, grade=True)

        async with factory() as session:
            candidate, _ = await EvalService(session).create_experiment(
                dataset=dataset,
                config=_config(
                    settings=settings,
                    dataset_hash=manifest.source_sha256,
                    dataset_id=manifest.dataset_id,
                    dataset_version=manifest.dataset_version,
                    git_commit=git_commit,
                    variant_role="candidate",
                    baseline_experiment_id=baseline.id,
                    agent_variant="full_agent_v1",
                ),
            )
            await session.commit()

        candidate_report = await ExperimentRunner(
            session_factory=factory, settings=settings
        ).run_experiment_and_grade(candidate.id, dataset, grade=True)

        async with factory() as session:
            baseline_rows = await EvalRepository(session).list_trials(baseline.id)
            candidate_rows = await EvalRepository(session).list_trials(candidate.id)

        judge = build_pairwise_judge(settings)
        comparison_group = f"b0-b3-live-smoke:{baseline.id}:{candidate.id}"
        baseline_by_case = {trial.case_id: trial for trial in baseline_rows}
        candidate_by_case = {trial.case_id: trial for trial in candidate_rows}
        judge_results: list[dict[str, object]] = []
        skipped_judge_cases: list[dict[str, str]] = []
        async with factory() as session:
            service = EvalService(session)
            for case_id in cases:
                baseline_trial = baseline_by_case[case_id]
                candidate_trial = candidate_by_case[case_id]
                if (
                    baseline_trial.status != "completed"
                    or candidate_trial.status != "completed"
                ):
                    skipped_judge_cases.append(
                        {
                            "case_id": case_id,
                            "baseline_status": baseline_trial.status,
                            "candidate_status": candidate_trial.status,
                            "reason": "both trials must be completed",
                        }
                    )
                    continue
                for position in (
                    PositionVariant.BASELINE,
                    PositionVariant.SWAPPED,
                ):
                    judge_run_id = uuid5(
                        NAMESPACE_URL,
                        f"{comparison_group}:{case_id}:{position.value}",
                    )
                    pair, result = await service.run_pairwise_judge(
                        baseline_trial_id=baseline_trial.id,
                        candidate_trial_id=candidate_trial.id,
                        case_id=case_id,
                        comparison_group_id=comparison_group,
                        judge_run_id=judge_run_id,
                        judge=judge,
                        position_variant=position,
                    )
                    judge_results.append(
                        {
                            "case_id": case_id,
                            "pair_id": str(pair.id),
                            "judge_run_id": str(judge_run_id),
                            "position": position.value,
                            "status": result.judge_run_status,
                            "normalized_winner": result.normalized_winner,
                            "confidence": result.confidence,
                            "latency_ms": result.latency_ms,
                        }
                    )
            await session.commit()

        return {
            "ok": True,
            "cases": cases,
            "agent_model": settings.llm_model,
            "judge_model": settings.judge_llm_model,
            "baseline": baseline_report.to_dict(),
            "candidate": candidate_report.to_dict(),
            "baseline_trial_statuses": {
                row.case_id: row.status for row in baseline_rows
            },
            "candidate_trial_statuses": {
                row.case_id: row.status for row in candidate_rows
            },
            "judge_results": judge_results,
            "skipped_judge_cases": skipped_judge_cases,
        }
    finally:
        await engine.dispose()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--cases", nargs="+", default=list(_DEFAULT_CASES))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = asyncio.run(
        run_smoke(cases=args.cases, git_commit=args.git_commit)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
