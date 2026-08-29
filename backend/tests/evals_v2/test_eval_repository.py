"""PostgreSQL constraints and Eval control-plane service tests."""

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.models.eval import EvalExperiment, EvalTrial
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import ExperimentCreate, GradeResult
from evals.v2.dataset_loader import load_dataset


def _config() -> ExperimentCreate:
    manifest = load_dataset().manifest
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
async def test_stage5_dataset_creates_experiment_and_empty_trials_without_scores(
    db_session: AsyncSession,
) -> None:
    experiment, trials = await EvalService(db_session).create_experiment(
        dataset=load_dataset(), config=_config()
    )

    assert experiment.status == "draft"
    assert len(trials) == 33  # 30 mock + 3 live-only memory cases
    assert all(trial.status == "pending" and trial.run_id is None for trial in trials)
    async with session_transaction(db_session):
        scores = await EvalRepository(db_session).list_scores(trials[0].id)
    assert scores == []


@pytest.mark.asyncio
async def test_duplicate_trial_is_rejected_by_database(db_session: AsyncSession) -> None:
    experiment, trials = await EvalService(db_session).create_experiment(
        dataset=load_dataset(), config=_config()
    )
    original = trials[0]
    duplicate = EvalTrial(
        experiment_id=experiment.id,
        case_id=original.case_id,
        case_fixture_hash=original.case_fixture_hash,
        trial_index=original.trial_index,
        seed=original.seed,
        run_type="evaluation",
        status="pending",
    )
    with pytest.raises(IntegrityError):
        async with session_transaction(db_session):
            await EvalRepository(db_session).create_trials([duplicate])


@pytest.mark.asyncio
async def test_illegal_experiment_transition_is_rejected(db_session: AsyncSession) -> None:
    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=load_dataset(), config=_config()
    )

    with pytest.raises(AppError) as error:
        await EvalService(db_session).transition_experiment(experiment.id, "completed")
    assert error.value.code == "EVAL_EXPERIMENT_TRANSITION_INVALID"


@pytest.mark.asyncio
async def test_started_experiment_versions_are_database_immutable(
    db_session: AsyncSession,
) -> None:
    service = EvalService(db_session)
    experiment, _ = await service.create_experiment(dataset=load_dataset(), config=_config())
    await service.transition_experiment(experiment.id, "running")

    with pytest.raises(DBAPIError, match="configuration is immutable"):
        async with session_transaction(db_session):
            await db_session.execute(
                update(EvalExperiment)
                .where(EvalExperiment.id == experiment.id)
                .values(prompt_version="tampered-v2")
            )


@pytest.mark.asyncio
async def test_pending_trial_cannot_receive_pseudo_grade(db_session: AsyncSession) -> None:
    _, trials = await EvalService(db_session).create_experiment(
        dataset=load_dataset(), config=_config()
    )
    grade = GradeResult(
        grader_name="always_true_is_forbidden",
        grader_version="v1",
        domain="system",
        metric_type="boolean",
        passed=True,
        hard_gate=True,
        evidence={"expected": "real runtime", "actual": "not run"},
        rationale="This must be rejected before persistence.",
    )

    with pytest.raises(AppError) as error:
        await EvalService(db_session).add_grade(trials[0].id, grade)
    assert error.value.code == "EVAL_TRIAL_NOT_GRADEABLE"
