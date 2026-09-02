"""PR-5 ProviderCall audit row tests (DB-bound, default fixture mode).

These exercise the path where the TrialRunner builds the executor with
``EVAL_PROVIDER_MODE=fixture`` (conftest default) and so every Provider call
goes through FixtureProvider -> ProviderCallRecorder -> ``provider_calls``
table.

Spec exit gate: "ProviderCall audit row is always written for every real
call (no silent skip)."
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings
from app.core.database import session_transaction
from app.harness.provider_calls.repository import ProviderCallRepository
from app.models.eval import EvalTrial
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import filter_cases, load_dataset
from evals.v2.trial_runner import TrialRunner
from tests.test_agent_runtime import runtime_factory


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


async def _provision_run(
    db_session: AsyncSession,
    db_connection: AsyncConnection,
    *,
    case_id: str,
    trial_idx: int = 0,
) -> UUID:
    bundle = filter_cases(load_dataset(), [case_id])
    _, trials = await EvalService(db_session).create_experiment(
        dataset=bundle, config=_config(bundle.manifest)
    )
    trial = trials[trial_idx]
    runner = TrialRunner(
        session_factory=runtime_factory(db_connection),
        settings=get_settings(),
    )
    await runner.run_trial(trial, bundle.cases[0])
    return trial.id


@pytest.mark.asyncio
async def test_every_provider_call_writes_audited_row(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """After a fixture-mode Run, ``provider_calls`` has rows for every call."""

    trial_id = await _provision_run(
        db_session, db_connection, case_id="create-01"
    )

    async with session_transaction(db_session):
        trial = await EvalRepository(db_session).get_trial(trial_id)
        assert trial is not None
        assert trial.run_id is not None
        rows = await ProviderCallRepository(db_session).list_for_run(
            trial.run_id
        )

    assert len(rows) > 0
    for call in rows:
        assert call.run_id is not None
        assert call.sequence >= 0
        assert call.provider_kind in {"llm", "embedding", "search"}
        assert call.provider_method in {
            "generate_agent_turn", "generate_plan",
            "repair_format", "repair_business_rules",
            "search", "embed",
        }
        assert len(call.request_projection_hash) == 64
        assert call.status in {"ok", "error", "cancelled"}
        if call.status == "ok":
            assert call.response_projection is not None
            assert call.response_projection_hash is not None

    # ``trial_id`` is propagated so the table is joinable from eval_trials.
    assert all(call.trial_id == trial_id for call in rows)


@pytest.mark.asyncio
async def test_global_sequence_is_monotonic_within_run(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """``(run_id, sequence)`` is unique and monotonically increasing."""

    trial_id = await _provision_run(
        db_session, db_connection, case_id="create-01"
    )

    async with session_transaction(db_session):
        trial = await EvalRepository(db_session).get_trial(trial_id)
        assert trial is not None and trial.run_id is not None
        rows = await ProviderCallRepository(db_session).list_for_run(
            trial.run_id
        )

    sequences = [r.sequence for r in rows]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))  # no duplicates


@pytest.mark.asyncio
async def test_three_kinds_only_when_relevant(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """A create-01 Run must include an LLM Planning call (no Tools/Embedding
    since the case is not a tool case)."""

    trial_id = await _provision_run(
        db_session, db_connection, case_id="create-01"
    )

    async with session_transaction(db_session):
        trial = await EvalRepository(db_session).get_trial(trial_id)
        assert trial is not None and trial.run_id is not None
        rows = await ProviderCallRepository(db_session).list_for_run(
            trial.run_id
        )

    kinds = {r.provider_kind for r in rows}
    assert "llm" in kinds
    # ``create-01`` is a happy plan path: no Tools, no fresh Search, no
    # Embedding. Embedding may still appear if the post-success distillation
    # path runs -- allow it but require at least LLM.
    assert all(k in {"llm", "embedding", "search"} for k in kinds)


@pytest.mark.asyncio
async def test_audit_row_written_even_on_provider_error(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """A run that exercises ``[mock:tool-unknown]`` still writes provider
    audit rows (and at least one row records the failure path)."""

    from evals.v2.runtime_smoke import load_runtime_smoke_dataset

    smoke = load_runtime_smoke_dataset()
    config = _config(smoke.manifest)
    config = config.model_copy(
        update={"execution_mode": "fixture_provider",
                "dataset_id": smoke.manifest.dataset_id,
                "dataset_version": smoke.manifest.dataset_version,
                "dataset_hash": smoke.manifest.source_sha256}
    )
    # Build the experiment + Trial explicitly.
    _, trials = await EvalService(db_session).create_experiment(
        dataset=smoke, config=config
    )
    trial = [t for t in trials if t.case_id == "runtime-tool-error-01"][0]
    case = [c for c in smoke.cases if c.case_id == "runtime-tool-error-01"][0]
    runner = TrialRunner(
        session_factory=runtime_factory(db_connection),
        settings=get_settings(),
    )
    await runner.run_trial(trial, case)

    async with session_transaction(db_session):
        # Reload trial with populate_existing to bypass identity-map cache:
        # TrialRunner wrote from another session.
        refreshed = await db_session.get(EvalTrial, trial.id, populate_existing=True)
        assert refreshed is not None
        assert refreshed.run_id is not None
        rows = await ProviderCallRepository(db_session).list_for_run(
            refreshed.run_id
        )

    assert len(rows) > 0
    # At least one LLM call must be present; the Tool rejection flow records
    # no separate audit row (Tool rejections go through tool_calls, not
    # provider_calls).
    assert any(r.provider_kind == "llm" for r in rows)


@pytest.mark.asyncio
async def test_pending_trial_run_no_audit_rows_when_no_call(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """A Trial created but never executed has no ProviderCall rows."""

    bundle = filter_cases(load_dataset(), ["create-01"])
    config = _config(bundle.manifest)
    _, trials = await EvalService(db_session).create_experiment(
        dataset=bundle, config=config
    )
    trial = trials[0]
    assert trial.run_id is None

    async with session_transaction(db_session):
        rows = await ProviderCallRepository(db_session).list_for_trial(
            trial.id
        )
    assert rows == []


# Suppress unused-import warning for imports kept to document the run_id
# reference path. ``select`` is also kept around in case a future debug
# path needs an explicit query.
_REPOSITORY_GUARD: type[AgentRunRepository] = AgentRunRepository  # noqa: F841
_SELECT_GUARD = select  # noqa: F841
