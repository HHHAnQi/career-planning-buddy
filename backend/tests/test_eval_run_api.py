"""PR-7 HTTP control plane tests for /api/v1/eval/runs.

The StubEvalRunnerExecutor (conftest.py) records submissions without
spawning a Task -- so we exercise the POST/GET contract at the HTTP layer
without driving the real Runtime. Service-layer behaviour (ExperimentRunner,
grading, build_report aggregation, executor recovery) is covered by separate
tests in this file and tests/evals_v2/.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from http import HTTPStatus
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.agent.eval_executor import EvalRunnerExecutor
from app.core.config import get_settings
from app.core.security import TokenService
from app.models.eval import EvalExperiment
from app.models.user import User
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import filter_cases, load_dataset
from tests.conftest import StubEvalRunnerExecutor
from tests.test_profile_api import bearer, guest_login


@pytest.fixture
def eval_executor(api_application: FastAPI) -> StubEvalRunnerExecutor:
    from app.api.dependencies import get_eval_runner_executor

    executor = api_application.dependency_overrides[get_eval_runner_executor]()
    assert isinstance(executor, StubEvalRunnerExecutor)
    return executor


async def _dev_login(client: AsyncClient, db_session: AsyncSession) -> str:
    """Issue a dev-role token for the freshly-logged-in guest user."""

    _guest_token, user_id_raw, _ = await guest_login(client)
    user = await db_session.get(User, UUID(user_id_raw))
    assert user is not None
    user.role = "dev"
    await db_session.flush()
    return TokenService(get_settings()).issue(user_id=user.id, role="dev")


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


# ---------------------------------------------------------------------------
# POST /api/v1/eval/runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_eval_run_returns_202_and_submits_to_executor(
    api_client: AsyncClient,
    db_session: AsyncSession,
    eval_executor: StubEvalRunnerExecutor,
) -> None:
    """POST creates a draft Experiment + Trials and stubs an executor submit."""

    dev_token = await _dev_login(api_client, db_session)

    response = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "trial_count": 1, "grade": False},
        headers=bearer(dev_token),
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    body = response.json()
    assert body["status"] == "draft"
    experiment_id = UUID(body["experiment_id"])
    assert body["status_url"].endswith(f"/api/v1/eval/runs/{experiment_id}")
    assert body["report_url"].endswith(f"/api/v1/eval/runs/{experiment_id}/report")

    # Stub recorded the submission with grade=False.
    assert len(eval_executor.submitted) == 1
    submitted_id, grade = eval_executor.submitted[0]
    assert submitted_id == experiment_id
    assert grade is False

    # EvalExperiment + two EvalTrial rows persisted as draft/pending.
    persisted = await db_session.get(EvalExperiment, experiment_id)
    assert persisted is not None
    assert persisted.status == "draft"
    assert persisted.git_commit != ""
    assert persisted.graph_version == "stage6b-v1"
    assert persisted.feature_stage == 6
    assert persisted.search_version == "mock-search-v1"
    assert persisted.eval_harness_version == "eval-harness-v2"
    trials = await EvalRepository(db_session).list_trials(experiment_id)
    assert len(trials) == 2
    assert all(t.status == "pending" for t in trials)


@pytest.mark.asyncio
async def test_create_eval_run_rejects_non_dev_role(
    api_client: AsyncClient,
) -> None:
    """Guest token (role=user) is rejected with 403 AUTH_FORBIDDEN."""

    token, _, _ = await guest_login(api_client)
    response = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "stage5"},
        headers=bearer(token),
    )
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["error"]["code"] == "AUTH_FORBIDDEN"


# ---------------------------------------------------------------------------
# GET /api/v1/eval/runs/{experiment_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_eval_run_status_returns_trials(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    dev_token = await _dev_login(api_client, db_session)
    create = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "trial_count": 1, "grade": False},
        headers=bearer(dev_token),
    )
    experiment_id = create.json()["experiment_id"]

    response = await api_client.get(
        f"/api/v1/eval/runs/{experiment_id}",
        headers=bearer(dev_token),
    )

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["status"] == "draft"
    assert body["trial_count"] == 2
    assert len(body["trials"]) == 2
    assert {t["status"] for t in body["trials"]} == {"pending"}
    assert body["graph_version"] == "stage6b-v1"
    assert body["feature_stage"] == 6
    assert body["search_version"] == "mock-search-v1"
    assert body["eval_harness_version"] == "eval-harness-v2"


@pytest.mark.asyncio
async def test_create_candidate_preserves_baseline_and_agent_variant(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    dev_token = await _dev_login(api_client, db_session)
    baseline_response = await api_client.post(
        "/api/v1/eval/runs",
        json={
            "dataset": "runtime-smoke",
            "cases": ["runtime-tool-error-01"],
            "trial_count": 1,
            "grade": False,
        },
        headers=bearer(dev_token),
    )
    baseline_id = baseline_response.json()["experiment_id"]

    candidate_response = await api_client.post(
        "/api/v1/eval/runs",
        json={
            "dataset": "runtime-smoke",
            "cases": ["runtime-tool-error-01"],
            "trial_count": 1,
            "grade": False,
            "baseline_experiment_id": baseline_id,
            "agent_variant": "compact_execution_v1",
        },
        headers=bearer(dev_token),
    )

    assert candidate_response.status_code == HTTPStatus.ACCEPTED
    candidate_id = candidate_response.json()["experiment_id"]
    status_response = await api_client.get(
        f"/api/v1/eval/runs/{candidate_id}", headers=bearer(dev_token)
    )
    assert status_response.status_code == HTTPStatus.OK
    body = status_response.json()
    assert body["variant_role"] == "candidate"
    assert body["baseline_experiment_id"] == baseline_id
    assert body["agent_variant"] == "compact_execution_v1"


@pytest.mark.asyncio
async def test_get_eval_run_status_404_on_missing_experiment(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    dev_token = await _dev_login(api_client, db_session)
    from uuid import uuid4

    response = await api_client.get(
        f"/api/v1/eval/runs/{uuid4()}",
        headers=bearer(dev_token),
    )
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()["error"]["code"] == "EVAL_EXPERIMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /api/v1/eval/runs/{experiment_id}/report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_eval_run_report_returns_409_when_running(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A still-running Experiment refuses the report endpoint with 409."""

    dev_token = await _dev_login(api_client, db_session)
    create = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "trial_count": 1, "grade": False},
        headers=bearer(dev_token),
    )
    experiment_id = UUID(create.json()["experiment_id"])

    # Manually flip the row to "running" to simulate the executor's progress.
    exp = await db_session.get(EvalExperiment, experiment_id)
    assert exp is not None
    exp.status = "running"
    await db_session.flush()

    response = await api_client.get(
        f"/api/v1/eval/runs/{experiment_id}/report",
        headers=bearer(dev_token),
    )
    assert response.status_code == HTTPStatus.CONFLICT
    assert response.json()["error"]["code"] == "EVAL_RUN_NOT_FINISHED"


@pytest.mark.asyncio
async def test_get_eval_run_report_returns_200_when_experiment_is_terminal(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A terminal (failed) Experiment is served by the report endpoint.

    Drives ``create_experiment -> running -> failed`` without executing a
    Trial. ``EvalService.build_report`` then returns a zero-score projection
    (no Trial is completed, no Score rows). Full report-with-scores coverage
    lives in tests/evals_v2/test_experiment_driver.py (PR-6) via the real
    TrialRunner grading flow, which is impractical to spin up inline here
    because Trial completion requires the ``ck_eval_trials_completed_outcome``
    columns (run_id + outcome_snapshot + transcript_hash) seeded from a real
    Runtime pass.
    """

    dev_token = await _dev_login(api_client, db_session)

    bundle = filter_cases(load_dataset(), ["create-01"])
    config = _stage5_config(bundle.manifest)
    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=bundle, config=config
    )
    await EvalService(db_session).transition_experiment(experiment.id, "running")
    await EvalService(db_session).transition_experiment(experiment.id, "failed")

    response = await api_client.get(
        f"/api/v1/eval/runs/{experiment.id}/report",
        headers=bearer(dev_token),
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["experiment_status"] == "failed"
    assert body["trial_count"] == 1
    assert body["scored_trial_count"] == 0
    assert body["any_score_generated"] is False
    assert body["hard_gate_pass_fraction"] == 0.0
    # trials array still enumerates the pending Trial row.
    assert len(body["trials"]) == 1


# ---------------------------------------------------------------------------
# EvalRunnerExecutor recovery
#
# recover_interrupted() opens its own session, so this test binds both setup
# and recovery sessions to the fixture connection using nested savepoints.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_executor_recovers_pending_and_running_trials(
    db_connection: AsyncConnection,
) -> None:
    session_factory = async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    bundle = filter_cases(load_dataset(), ["create-01", "create-02"])

    async with session_factory() as session:
        experiment, trials = await EvalService(session).create_experiment(
            dataset=bundle,
            config=_stage5_config(bundle.manifest),
        )
        await EvalService(session).transition_experiment(experiment.id, "running")
        await EvalRepository(session).mark_trial_running(
            trials[1].id,
            started_at=datetime.now(UTC),
        )
        await session.commit()

    executor = EvalRunnerExecutor(
        session_factory=session_factory,
        settings=get_settings(),
    )
    assert await executor.recover_interrupted() == 1

    async with session_factory() as session:
        recovered_experiment = await EvalRepository(session).get_experiment(
            experiment.id
        )
        recovered_trials = await EvalRepository(session).list_trials(experiment.id)

    assert recovered_experiment is not None
    assert recovered_experiment.status == "failed"
    assert [trial.status for trial in recovered_trials] == ["cancelled", "failed"]
    assert {trial.error_code for trial in recovered_trials} == {
        "PROCESS_INTERRUPTED"
    }


@pytest.mark.asyncio
async def test_eval_executor_cancellation_converges_experiment_and_trials(
    db_connection: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = async_sessionmaker(
        bind=db_connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    bundle = filter_cases(load_dataset(), ["create-01", "create-02"])

    async with session_factory() as session:
        experiment, trials = await EvalService(session).create_experiment(
            dataset=bundle,
            config=_stage5_config(bundle.manifest),
        )
        await EvalService(session).transition_experiment(experiment.id, "running")
        await EvalRepository(session).mark_trial_running(
            trials[0].id,
            started_at=datetime.now(UTC),
        )
        await session.commit()

    async def cancelled_run(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "evals.v2.experiment_runner.ExperimentRunner.run_experiment_and_grade",
        cancelled_run,
    )
    executor = EvalRunnerExecutor(
        session_factory=session_factory,
        settings=get_settings(),
    )
    await executor._execute(experiment.id, bundle, grade=True)  # noqa: SLF001

    async with session_factory() as session:
        cancelled_experiment = await EvalRepository(session).get_experiment(
            experiment.id
        )
        cancelled_trials = await EvalRepository(session).list_trials(experiment.id)
        assert cancelled_experiment is not None
        assert cancelled_experiment.status == "cancelled"
        assert [trial.status for trial in cancelled_trials] == [
            "cancelled",
            "cancelled",
        ]
        assert {trial.error_code for trial in cancelled_trials} == {
            "USER_REQUESTED_CANCEL"
        }

        # The convergence operation is idempotent and keeps terminal evidence.
        await EvalService(session).finalize_cancelled_experiment(experiment.id)
        repeated = await EvalRepository(session).list_trials(experiment.id)
        assert [trial.status for trial in repeated] == ["cancelled", "cancelled"]



# ---------------------------------------------------------------------------
# EvalService.build_report aggregates Scores directly from DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_service_build_report_aggregates_scores(
    db_session: AsyncSession,
) -> None:
    """build_report returns zero-score report without re-running the runtime."""

    bundle = filter_cases(load_dataset(), ["create-01"])
    config = _stage5_config(bundle.manifest)
    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=bundle, config=config
    )

    report = await EvalService(db_session).build_report(experiment.id, bundle)
    assert report.experiment_status == "draft"
    assert report.trial_count == 1
    assert report.scored_trial_count == 0
    assert report.any_score_generated is False
    assert report.hard_gate_pass_fraction == 0.0
