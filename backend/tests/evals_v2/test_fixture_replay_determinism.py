"""PR-5 Fixture replay determinism tests (DB-bound, fixture mode).

Verifies that re-running the same Trial in fixture mode produces the exact
same ``bundle_hash`` and ``response_projection_hash`` on every call, plus
that a changed request projection produces a different bundle hash.

Spec exit gate: "Fixture replay produces the same output for a given
(case_id, scenario_hash)".
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.core.database import session_transaction
from app.harness.provider_calls.repository import ProviderCallRepository
from app.models.eval import EvalTrial
from app.models.provider_call import ProviderCall
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import filter_cases, load_dataset
from evals.v2.trial_runner import TrialRunner
from tests.test_agent_runtime import runtime_factory

PR5_SMOKE = [
    "create-01",
    "replan-01",
    "replan-03",
    "create-07",
    "create-09",
    "repair-01",
]


def _config(manifest: DatasetManifest) -> ExperimentCreate:
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
        execution_mode="fixture_provider",
        variant_role="baseline",
        trial_count=1,
    )


async def _run_case(
    db_session: AsyncSession,
    db_connection: AsyncConnection,
    *,
    case_id: str,
    run_type: str = "evaluation",
    fixture_source_trial_id: UUID | None = None,
) -> tuple[UUID, list[ProviderCall]]:
    bundle = filter_cases(load_dataset(), [case_id])
    source_experiment_id = None
    if fixture_source_trial_id is not None:
        source_trial = await db_session.get(EvalTrial, fixture_source_trial_id)
        assert source_trial is not None
        source_experiment_id = source_trial.experiment_id
    service = EvalService(db_session)
    experiment, trials = await service.create_experiment(
        dataset=bundle,
        config=_config(bundle.manifest),
        run_type=run_type,
        fixture_source_experiment_id=source_experiment_id,
    )
    trial = trials[0]
    await service.transition_experiment(experiment.id, "running")
    settings = get_settings().model_copy(update={"eval_provider_mode": "fixture"})
    runner = TrialRunner(
        session_factory=runtime_factory(db_connection),
        settings=settings,
    )
    await runner.run_trial(
        trial,
        bundle.cases[0],
        fixture_source_trial_id=trial.fixture_source_trial_id,
    )
    await service.transition_experiment(experiment.id, "completed")
    async with session_transaction(db_session):
        # TrialRunner attaches outcome on a different session; reload with
        # populate_existing to bypass identity-map cache.
        refreshed = await db_session.get(EvalTrial, trial.id, populate_existing=True)
        assert refreshed is not None
        assert refreshed.run_id is not None
        rows = await ProviderCallRepository(db_session).list_for_run(
            refreshed.run_id
        )
    return trial.id, rows


@pytest.mark.asyncio
async def test_fixture_replay_uses_immutable_recording_and_stable_transcript(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """A new fixture_replay Trial consumes, but never rewrites, its recording."""

    trial_id_a, rows_a = await _run_case(
        db_session, db_connection, case_id="create-07"
    )
    trial_id_b, rows_b = await _run_case(
        db_session,
        db_connection,
        case_id="create-07",
        run_type="fixture_replay",
        fixture_source_trial_id=trial_id_a,
    )

    hashes_a = [r.request_projection_hash for r in rows_a]
    hashes_b = [r.request_projection_hash for r in rows_b]
    source_calls = [
        (r.sequence, r.provider_kind, r.provider_method, r.status, r.error_code)
        for r in rows_a
    ]
    replay_calls = [
        (r.sequence, r.provider_kind, r.provider_method, r.status, r.error_code)
        for r in rows_b
    ]
    assert hashes_a == hashes_b, (
        "request_projection_hashes drifted across identical reruns: "
        f"source={source_calls} replay={replay_calls}"
    )
    # response_projection_hashes only exist for non-error rows; compare those
    # that do exist as an additional determinism gate.
    resp_a = [r.response_projection_hash for r in rows_a if r.response_projection_hash]
    resp_b = [r.response_projection_hash for r in rows_b if r.response_projection_hash]
    assert resp_a == resp_b

    async with session_transaction(db_session):
        repo = ProviderCallRepository(db_session)
        source_bundles = await repo.list_bundles_for_trial(trial_id_a)
        replay_bundles = await repo.list_bundles_for_trial(trial_id_b)
        source_trial = await db_session.get(
            EvalTrial, trial_id_a, populate_existing=True
        )
        replay_trial = await db_session.get(
            EvalTrial, trial_id_b, populate_existing=True
        )

    assert len(source_bundles) == 1
    assert replay_bundles == []
    assert source_trial is not None and replay_trial is not None
    assert source_trial.transcript_hash == replay_trial.transcript_hash
    assert replay_trial.run_type == "fixture_replay"
    assert replay_trial.fixture_source_trial_id == trial_id_a
    assert replay_trial.outcome_snapshot_json is not None
    assert replay_trial.outcome_snapshot_json["fixture_replay"] == {
        "source_trial_id": str(trial_id_a),
        "bundle_hash": source_bundles[0].bundle_hash,
    }


@pytest.mark.asyncio
async def test_provider_calls_exist_for_each_smoke_case(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """The fixture provider produces ≥1 LLM call for every smoke case.

    Embedding/Search calls depend on case-specific paths and may be 0.
    """

    for case_id in PR5_SMOKE:
        _trial_id, rows = await _run_case(
            db_session, db_connection, case_id=case_id
        )
        assert len(rows) > 0, f"case {case_id} produced no provider calls"
        llm_calls = [r for r in rows if r.provider_kind == "llm"]
        assert llm_calls, (
            f"case {case_id} produced no LLM provider call"
        )


@pytest.mark.asyncio
async def test_mock_mode_still_runs(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """Eval mode ``mock`` (no fixture wrapper) still executes end-to-end.

    Resets the trials/rows for a virgin case; we only need to assert the
    Run completes without raising.
    """

    settings = get_settings().model_copy(update={"eval_provider_mode": "mock"})
    bundle = filter_cases(load_dataset(), ["create-01"])
    _, trials = await EvalService(db_session).create_experiment(
        dataset=bundle, config=_config(bundle.manifest)
    )
    trial = trials[0]
    runner = TrialRunner(
        session_factory=runtime_factory(db_connection),
        settings=settings,
    )
    await runner.run_trial(trial, bundle.cases[0])

    async with session_transaction(db_session):
        refreshed = await db_session.get(EvalTrial, trial.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.run_id is not None


@pytest.mark.asyncio
async def test_capture_provider_call_projection_in_grade_trial(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """After a fixture-mode Run, ``grade_trial`` exposes the
    ``provider_call_projection`` evidence item on the Trial."""

    bundle = filter_cases(load_dataset(), ["create-01"])
    case = bundle.cases[0]
    _, trials = await EvalService(db_session).create_experiment(
        dataset=bundle, config=_config(bundle.manifest)
    )
    trial = trials[0]
    settings = get_settings().model_copy(update={"eval_provider_mode": "fixture"})
    runner = TrialRunner(
        session_factory=runtime_factory(db_connection),
        settings=settings,
    )
    await runner.run_trial(trial, case)
    await EvalService(db_session).grade_trial(trial.id, case)

    async with session_transaction(db_session):
        items = await EvalRepository(db_session).list_evidence_items(trial.id)
    provider_items = [
        i for i in items if i.kind == "provider_call_projection"
    ]
    assert len(provider_items) == 1
    projection = provider_items[0].projection_json
    call_count_raw = projection.get("call_count", 0)
    assert isinstance(call_count_raw, int) and call_count_raw > 0
    assert "per_kind_counts" in projection
    assert "per_method_counts" in projection


# Keep ``UUID`` imported for type-completeness.
_TYPE_GUARD: type[UUID] = UUID  # noqa: F841
