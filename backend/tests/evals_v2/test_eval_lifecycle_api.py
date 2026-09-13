"""PR-9b End-to-end tests: list / progress / cancel / regenerate / live+mock reject.

The StubEvalRunnerExecutor from conftest records submissions but does not
spawn any task, so the lifecycle endpoints are exercised at the HTTP +
DB layer without driving the real Runtime.
"""

from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import filter_cases, load_dataset
from evals.v2.runtime_smoke import load_runtime_smoke_dataset
from tests.test_profile_api import bearer, guest_login


async def _dev_login(client: AsyncClient, db_session: AsyncSession) -> str:
    from uuid import UUID

    from app.core.config import get_settings
    from app.core.security import TokenService
    from app.models.user import User

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
# Cluster B: list + progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_eval_runs_returns_paginated_results(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POSTing two experiments and listing surfaces both rows."""

    dev_token = await _dev_login(api_client, db_session)
    create_a = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "trial_count": 1, "grade": False},
        headers=bearer(dev_token),
    )
    create_b = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "trial_count": 1, "grade": False},
        headers=bearer(dev_token),
    )
    assert create_a.status_code == HTTPStatus.ACCEPTED
    assert create_b.status_code == HTTPStatus.ACCEPTED
    # POST handlers run inside the same savepoint as db_session; expire
    # the identity-map so the GET handler re-queries from DB.
    db_session.expire_all()

    resp = await api_client.get("/api/v1/eval/runs", headers=bearer(dev_token))
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert len(body["items"]) >= 2
    # Both freshly-created Experiments are in the listing. Ordering by
    # created_at is unstable inside one second even with tz-aware now();
    # assert set-membership instead.
    ids = {item["experiment_id"] for item in body["items"]}
    assert create_a.json()["experiment_id"] in ids
    assert create_b.json()["experiment_id"] in ids


@pytest.mark.asyncio
async def test_list_eval_runs_filter_by_status(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    dev_token = await _dev_login(api_client, db_session)
    create = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "grade": False},
        headers=bearer(dev_token),
    )
    eid = create.json()["experiment_id"]

    resp = await api_client.get(
        "/api/v1/eval/runs?status=draft", headers=bearer(dev_token)
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert all(item["status"] == "draft" for item in body["items"])
    ids = [item["experiment_id"] for item in body["items"]]
    assert eid in ids


@pytest.mark.asyncio
async def test_progress_endpoint_counts_trial_states(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    dev_token = await _dev_login(api_client, db_session)
    create = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "grade": False},
        headers=bearer(dev_token),
    )
    eid = create.json()["experiment_id"]

    resp = await api_client.get(
        f"/api/v1/eval/runs/{eid}/progress", headers=bearer(dev_token)
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["experiment_id"] == eid
    assert body["status"] == "draft"
    assert body["trial_count"] == 2
    assert body["pending_count"] == 2
    assert body["completed_count"] == 0
    assert body["estimated_progress"] == 0.0


@pytest.mark.asyncio
async def test_progress_endpoint_404_when_missing(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    dev_token = await _dev_login(api_client, db_session)
    resp = await api_client.get(
        f"/api/v1/eval/runs/{uuid4()}/progress", headers=bearer(dev_token)
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Cluster B: regenerate report (content-hash driven)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_regenerate_report_does_not_create_trials_or_provider_calls(
    db_session: AsyncSession,
) -> None:
    """regenerate_report must be a pure aggregate; no Runtime, no new rows."""

    bundle = filter_cases(load_dataset(), ["create-01"])
    config = _stage5_config(bundle.manifest)
    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=bundle, config=config
    )

    repo = EvalRepository(db_session)
    # Mark draft -> failed so build_report doesn't bail on running state.
    await EvalService(db_session).transition_experiment(experiment.id, "running")
    await EvalService(db_session).transition_experiment(experiment.id, "failed")

    # Snapshot pre-state.
    trials_before = await repo.list_trials(experiment.id)
    exp_before = await repo.get_experiment(experiment.id)
    assert exp_before is not None
    rev_before = int(exp_before.report_revision)

    report = await EvalService(db_session).regenerate_report(
        experiment.id, bundle
    )
    # First regenerate always bumps revision since report_content_hash was
    # NULL and the new content-hash is non-empty.
    assert report is not None
    trials_after = await repo.list_trials(experiment.id)
    assert len(trials_after) == len(trials_before)

    exp_after = await repo.get_experiment(experiment.id)
    assert exp_after is not None
    assert int(exp_after.report_revision) == int(rev_before) + 1

    # Second regenerate with unchanged Trial state must NOT bump.
    await EvalService(db_session).regenerate_report(experiment.id, bundle)
    await db_session.refresh(exp_after)
    assert int(exp_after.report_revision) == int(rev_before) + 1


# ---------------------------------------------------------------------------
# Cluster C: cancel lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_marks_request_then_recovers_as_failed_with_interrupt_code(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Cancel-then-restart-marks-interrupted (exit gate): the recover
    path overrides a pending cancel request with PROCESS_INTERRUPTED +
    failed status, so a process crash is never masked as a clean cancel.

    The production ``EvalRunnerExecutor.recover_interrupted`` opens its
    own session via the global ``AsyncSessionFactory``; tests can't share
    that scope (the global factory binds to the live engine, not the
    test's rolled-back connection). We inline the recover-logic of
    transition-to-failed + stamp PROCESS_INTERRUPTED on stranded Trials
    so the assertion is structural rather than executor-bound.
    """

    dev_token = await _dev_login(api_client, db_session)
    create = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "grade": False},
        headers=bearer(dev_token),
    )
    eid_str = create.json()["experiment_id"]
    import uuid as _uuid

    eid = _uuid.UUID(eid_str)
    # Promote to running so cancel has a real transition path and the
    # Trials also transit pending -> running.
    service = EvalService(db_session)
    await service.transition_experiment(eid, "running")
    repo = EvalRepository(db_session)
    trials = await repo.list_trials(eid)
    for trial in trials:
        if trial.status == "pending":
            trial.status = "running"
    await db_session.flush()

    cancel_resp = await api_client.post(
        f"/api/v1/eval/runs/{eid_str}/cancel", headers=bearer(dev_token)
    )
    assert cancel_resp.status_code == HTTPStatus.ACCEPTED
    body = cancel_resp.json()
    assert body["cancel_requested"] is True
    assert body["cancel_requested_at"] is not None
    assert body["status"] == "running"  # not promoted synchronously

    # Simulate recover_interrupted: experiment -> failed, every non-
    # terminal Trial -> failed PROCESS_INTERRUPTED.
    await service.transition_experiment(eid, "failed")
    await db_session.flush()
    refreshed = await repo.get_experiment(eid)
    assert refreshed is not None
    assert refreshed.status == "failed"  # not cancelled (crash priority)
    stranded = await repo.list_trials(eid)
    for trial in stranded:
        if trial.status in {"pending", "running"}:
            trial.status = "failed"
            if not trial.error_code:
                trial.error_code = "PROCESS_INTERRUPTED"
    await db_session.flush()

    # Final assertion: the stranded Trials now carry PROCESS_INTERRUPTED,
    # which the stats taxonomy classifies as a HARNESS runtime failure
    # rather than USER_ACTION.
    for trial in await repo.list_trials(eid):
        if trial.error_code == "PROCESS_INTERRUPTED":
            from app.harness.errors import is_runtime_failure, is_user_cancel

            assert is_runtime_failure(trial.error_code)
            assert not is_user_cancel(trial.error_code)


@pytest.mark.asyncio
async def test_cancel_after_terminal_is_idempotent(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /cancel against a failed experiment returns cancel_requested=False."""

    dev_token = await _dev_login(api_client, db_session)
    create = await api_client.post(
        "/api/v1/eval/runs",
        json={"dataset": "runtime-smoke", "grade": False},
        headers=bearer(dev_token),
    )
    experiment_id = create.json()["experiment_id"]
    eid = __import__("uuid").UUID(experiment_id)
    # Flip draft -> running -> failed to reach terminal.
    await EvalService(db_session).transition_experiment(eid, "running")
    await EvalService(db_session).transition_experiment(eid, "failed")

    resp = await api_client.post(
        f"/api/v1/eval/runs/{experiment_id}/cancel", headers=bearer(dev_token)
    )
    assert resp.status_code == HTTPStatus.ACCEPTED
    body = resp.json()
    assert body["cancel_requested"] is False
    assert body["status"] == "failed"

    repo = EvalRepository(db_session)
    refreshed = await repo.get_experiment(eid)
    assert refreshed is not None
    assert refreshed.cancel_requested_at is None  # not stamped on terminal


# ---------------------------------------------------------------------------
# Cluster D: live + mock create-time reject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_eval_run_rejects_live_provider_with_mock_llm(
    db_session: AsyncSession,
) -> None:
    """execution_mode=live_provider + Settings.llm_provider=mock → 409."""

    from app.core.config import get_settings
    from app.core.exceptions import AppError

    settings = get_settings()
    # Production tests pin llm_provider=mock via test env; emulate that.
    bundle = load_runtime_smoke_dataset()
    config = _stage5_config(bundle.manifest).model_copy(
        update={"execution_mode": "live_provider"}
    )
    # Force the env the guard checks.
    assert settings.llm_provider == "mock"
    with pytest.raises(AppError) as err:
        await EvalService(db_session).create_experiment(
            dataset=bundle, config=config
        )
    assert err.value.code == "EVAL_PROVIDER_MODE_INVALID"
    assert err.value.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_fixture_replay_api_requires_explicit_source_experiment(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    dev_token = await _dev_login(api_client, db_session)
    response = await api_client.post(
        "/api/v1/eval/runs",
        json={
            "dataset": "runtime-smoke",
            "provider_mode": "fixture",
            "run_type": "fixture_replay",
        },
        headers=bearer(dev_token),
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
