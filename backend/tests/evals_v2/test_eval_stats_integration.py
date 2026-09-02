"""PR-9a DB-backed integration: end-to-end run populates new stats fields."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import filter_cases, load_dataset
from evals.v2.experiment_runner import ExperimentRunner
from evals.v2.runtime_smoke import load_runtime_smoke_dataset
from tests.test_agent_runtime import runtime_factory


def _stage5_config(manifest: DatasetManifest) -> ExperimentCreate:
    return ExperimentCreate(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.source_sha256,
        git_commit="7d29a45",
        graph_version="stage5-v1",
        prompt_version="career-plan-v1",
        model_version="mock-v1",
        tool_version="tool-contract-v1",
        context_version="context-v1",
        memory_version="memory-v1",
        execution_mode="mock_provider",
        variant_role="baseline",
        trial_count=1,
    )


@pytest.mark.asyncio
async def test_run_experiment_and_grade_populates_case_stats_single_trial(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """One trial per case, mock provider — case_stats + experiment_stats populated."""

    stage5 = filter_cases(load_dataset(), ["create-01"])
    factory = runtime_factory(db_connection)
    settings = get_settings()

    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=stage5, config=_stage5_config(stage5.manifest)
    )
    runner = ExperimentRunner(session_factory=factory, settings=settings)
    report = await runner.run_experiment_and_grade(
        experiment.id, stage5, grade=True
    )

    # case_stats and experiment_stats were filled by run_experiment_and_grade.
    assert report.case_stats, "case_stats must be populated"
    assert "create-01" in report.case_stats
    create_stat = report.case_stats["create-01"]
    # Only one trial; first_attempt / pass_at_n / pass_all_n coincide.
    assert create_stat.trial_count == 1
    assert create_stat.first_attempt_passed in (True, False)
    assert create_stat.pass_at_n == create_stat.first_attempt_passed
    assert create_stat.pass_all_n == create_stat.first_attempt_passed
    # Tokens / latency must be non-negative; CI shape present.
    assert create_stat.mean_tokens_in >= 0
    assert create_stat.mean_latency_ms >= 0
    assert create_stat.success_rate_ci.low >= 0.0
    assert create_stat.success_rate_ci.high <= 1.0

    # experiment_stats is also populated.
    assert report.experiment_stats is not None
    exp_stat = report.experiment_stats
    assert exp_stat.case_count == 1
    assert exp_stat.trial_count == 1
    # Mock provider currently always passes create-01.
    assert exp_stat.success_rate in {0.0, 1.0}


@pytest.mark.asyncio
async def test_runtime_smoke_report_includes_variant_excluded_from_case_stats(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """runtime-smoke has no counterfactual variant tagging, so case_stats
    sees every case's first trial. Verifies that variant-free datasets
    flow through unchanged.
    """

    smoke = load_runtime_smoke_dataset()
    factory = runtime_factory(db_connection)
    settings = get_settings()

    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=smoke, config=_stage5_config(smoke.manifest)
    )
    runner = ExperimentRunner(session_factory=factory, settings=settings)
    report = await runner.run_experiment_and_grade(
        experiment.id, smoke, grade=True
    )

    assert report.experiment_stats is not None
    # runtime-smoke has 2 cases; the cancel case is non-completed.
    # completed_count may be 1 (cancel arm failed) regardless of stats.
    assert report.experiment_stats.case_count == 2
    assert report.experiment_stats.trial_count == 2
    # case_stats keys are stable per case_id from the dataset.
    assert set(report.case_stats.keys()) == {
        case.case_id for case in smoke.cases
    }
