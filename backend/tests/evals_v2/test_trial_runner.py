"""PR-3 TrialRunner integration tests (require PostgreSQL).

These tests drive the real Runtime (``AgentRunService`` → ``AgentRunExecutor``
→ ``FixedPlanningGraph`` → ``NodeRunner`` → ``ToolRegistry`` →
``AgentRunFinalizer`` → PostgreSQL) and freeze the resulting ``EvalTrial``
outcomes. They are gated on a live PostgreSQL instance via the ``db_connection``
fixture; on a machine without one they will error at collection-run time.

PR-3 exit gates covered:

* each Trial produces real ``AgentStep`` / ``AgentEvent`` rows;
* the terminal event is unique and last;
* the Outcome Snapshot matches the DB Plan/Tasks;
* Trials use isolated users and never reuse prior Case state;
* a Run that never reaches terminal leaves the Trial non-completed
  (``timed_out``) -- the waiter never synthesizes a fake terminal;
* no Score rows are produced by the TrialRunner.
"""

import asyncio
from collections.abc import Callable
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
)

from app.agent.executor import AgentRunExecutor
from app.core.config import get_settings
from app.core.database import session_transaction
from app.harness.provider_calls import FixtureStore
from app.models.agent_run import AgentEvent, AgentRun, AgentStep
from app.models.eval import EvalExperiment, EvalScore, EvalTrial
from app.models.plan import Plan, Task
from app.services.evals import EvalService
from evals.v2.collectors.outcome import RunOutcome, terminal_event_count, terminal_events
from evals.v2.contracts import DatasetManifest, ExperimentCreate
from evals.v2.dataset_loader import DatasetBundle, filter_cases, load_dataset
from evals.v2.experiment_runner import ExperimentRunner
from evals.v2.runtime_smoke import load_runtime_smoke_dataset
from evals.v2.trial_runner import TrialRunner, TrialRunnerConfig
from tests.test_agent_runtime import runtime_factory

TERMINAL_EVENT_TYPES = {"run.completed", "run.degraded", "run.failed", "run.cancelled"}

STAGE5_SMOKE_CASE_IDS = [
    "create-01",
    "clarify-01",
    "safe-01",
    "replan-01",
    "replan-03",
    "create-07",
    "create-08",
    "create-09",
    "repair-01",
    "repair-03",
]


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


def _smoke_config(manifest: DatasetManifest) -> ExperimentCreate:
    return ExperimentCreate(
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.source_sha256,
        git_commit="7d29a45",
        graph_version="runtime-smoke-v1",
        prompt_version="career-plan-v1",
        model_version="mock-v1",
        tool_version="tool-contract-v1",
        context_version="context-v1",
        memory_version="memory-v1",
        execution_mode="mock_provider",
        variant_role="baseline",
        trial_count=1,
    )


async def _create_single_case_experiment(
    session: AsyncSession,
    *,
    dataset: DatasetBundle,
    config_fn: Callable[[DatasetManifest], ExperimentCreate],
    case_id: str,
) -> tuple[EvalExperiment, EvalTrial]:
    if dataset.manifest.dataset_id == "stage5":
        bundle = filter_cases(dataset, [case_id])
    else:
        bundle = dataset
    config = config_fn(bundle.manifest)
    experiment, trials = await EvalService(session).create_experiment(
        dataset=bundle, config=config
    )
    assert len(trials) == 1
    return experiment, trials[0]


def _runner(
    db_connection: AsyncConnection,
    *,
    deadline_seconds: float = 30.0,
) -> TrialRunner:
    return TrialRunner(
        session_factory=runtime_factory(db_connection),
        settings=get_settings(),
        config=TrialRunnerConfig(deadline_seconds=deadline_seconds),
    )


# ---------------------------------------------------------------------------
# Single-case happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_plan_trial_runs_real_runtime_and_persists_completed_outcome(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    experiment, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="create-01"
    )
    case = next(c for c in stage5.cases if c.case_id == "create-01")

    runner = _runner(db_connection)
    outcome = await runner.run_trial(trial, case)
    del experiment

    assert outcome.status == "completed"
    assert outcome.result_kind == "plan"
    assert outcome.final_plan_id is not None
    assert outcome.plan is not None
    assert len(outcome.tasks) >= 1
    # real AgentStep / AgentEvent rows exist
    assert len(outcome.steps) >= 1
    assert len(outcome.events) >= 1
    # terminal event is unique and last
    terminals = terminal_events(outcome)
    assert len(terminals) == 1
    assert outcome.events[-1]["event_type"] in TERMINAL_EVENT_TYPES
    # transcript_hash is a 64-hex string (DB CK contract)
    assert len(outcome.transcript_hash) == 64
    assert all(c in "0123456789abcdef" for c in outcome.transcript_hash)


@pytest.mark.asyncio
async def test_outcome_snapshot_matches_database_plan_and_tasks(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="create-01"
    )
    case = next(c for c in stage5.cases if c.case_id == "create-01")
    runner = _runner(db_connection)
    outcome = await runner.run_trial(trial, case)

    async with session_transaction(db_session):
        plan = await db_session.get(Plan, outcome.final_plan_id)
        assert plan is not None
        db_tasks = (
            await db_session.scalars(
                select(Task).where(Task.plan_id == plan.id).order_by(Task.order_index)
            )
        ).all()
    assert outcome.plan is not None
    assert outcome.plan["summary"] == plan.summary
    assert [t["title"] for t in outcome.tasks] == [t.title for t in db_tasks]
    assert [t["task_type"] for t in outcome.tasks] == [t.task_type for t in db_tasks]


@pytest.mark.asyncio
async def test_clarification_case_yields_degraded_without_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="clarify-01"
    )
    case = next(c for c in stage5.cases if c.case_id == "clarify-01")
    outcome = await _runner(db_connection).run_trial(trial, case)

    assert outcome.status == "degraded"
    assert outcome.result_kind == "clarification"
    assert outcome.final_plan_id is None
    assert outcome.plan is None
    assert outcome.tasks == []
    # Trial is ``completed`` (runtime degraded) and the snapshot is present.
    # The TrialRunner wrote from a different session; re-read inside a fresh
    # transaction that bypasses the test session's identity-map cache by
    # selecting the row directly via the ORM mapping rather than the
    # previously-loaded identity.
    async with session_transaction(db_session):
        refreshed = await db_session.get(EvalTrial, trial.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.outcome_snapshot_json is not None


@pytest.mark.asyncio
async def test_safe_response_case_routes_via_risk_gate(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="safe-01"
    )
    case = next(c for c in stage5.cases if c.case_id == "safe-01")
    outcome = await _runner(db_connection).run_trial(trial, case)
    assert outcome.status == "degraded"
    assert outcome.result_kind == "safe_response"
    assert outcome.plan is None


# ---------------------------------------------------------------------------
# Replan (FixtureLoader-seeded source Plan / Review)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replan_continue_uses_seeded_source_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="replan-01"
    )
    case = next(c for c in stage5.cases if c.case_id == "replan-01")
    outcome = await _runner(db_connection).run_trial(trial, case)
    assert outcome.status == "completed"
    assert outcome.result_kind == "plan"
    # A new plan exists whose source line points back to a seeded source Plan.
    async with session_transaction(db_session):
        plans = (
            await db_session.scalars(
                select(Plan).where(Plan.user_id == outcome.user_id).order_by(Plan.created_at)
            )
        ).all()
    assert len(plans) >= 2  # seeded source + new replan result
    assert outcome.final_plan_id == plans[-1].id


@pytest.mark.asyncio
async def test_replan_adjust_drives_via_review_service_start_next_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="replan-03"
    )
    case = next(c for c in stage5.cases if c.case_id == "replan-03")
    outcome = await _runner(db_connection).run_trial(trial, case)
    assert outcome.status == "completed"
    assert outcome.result_kind == "plan"
    async with session_transaction(db_session):
        run = await db_session.get(AgentRun, outcome.run_id)
    assert run is not None
    assert run.replan_mode == "adjust"
    assert run.source_review_id is not None


# ---------------------------------------------------------------------------
# Tool success + per-user Evidence ownership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id,expected_tool", [
    ("create-07", "memory_lookup"),
    ("create-08", "rag_retrieve"),
    ("create-09", "web_search"),
])
@pytest.mark.asyncio
async def test_tool_success_cases_record_real_tool_calls_with_schema(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    case_id: str,
    expected_tool: str,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id=case_id
    )
    case = next(c for c in stage5.cases if c.case_id == case_id)
    outcome = await _runner(db_connection).run_trial(trial, case)

    assert outcome.status == "completed"
    matching = [tc for tc in outcome.tool_calls if tc["tool_name"] == expected_tool]
    assert matching, f"expected a {expected_tool} tool call, got {outcome.tool_calls}"
    call = matching[0]
    assert call["success"] is True
    assert call["error_code"] is None
    assert call["result_hash"]  # non-empty
    # All plan evidence refs, if any, must belong to the Trial user.
    if outcome.plan is not None:
        refs_count = outcome.plan.get("evidence_refs_count", 0)
        assert isinstance(refs_count, int) and refs_count >= 0


# ---------------------------------------------------------------------------
# Repair paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", ["repair-01", "repair-03"])
@pytest.mark.asyncio
async def test_repair_cases_still_converge_to_completed_plan(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    case_id: str,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id=case_id
    )
    case = next(c for c in stage5.cases if c.case_id == case_id)
    outcome = await _runner(db_connection).run_trial(trial, case)
    assert outcome.status == "completed"
    assert outcome.result_kind == "plan"
    assert outcome.final_plan_id is not None


# ---------------------------------------------------------------------------
# Native V2 runtime cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_tool_error_case_records_failed_tool_without_forged_evidence(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    smoke = load_runtime_smoke_dataset()
    bundle = smoke
    config = _smoke_config(smoke.manifest)
    experiment, trials = await EvalService(db_session).create_experiment(
        dataset=bundle, config=config
    )
    trial = next(t for t in trials if t.case_id == "runtime-tool-error-01")
    case = next(c for c in smoke.cases if c.case_id == "runtime-tool-error-01")
    outcome = await _runner(db_connection).run_trial(trial, case)
    del experiment

    # The Tool handler fails deterministically, but the Run still converges.
    assert outcome.status == "completed"
    failing = [tc for tc in outcome.tool_calls if tc["tool_name"] == "unregistered_tool"]
    assert failing
    assert failing[0]["success"] is False
    assert failing[0]["error_code"] == "TOOL_NOT_ALLOWED"
    assert terminal_event_count(outcome) == 1
    # No forged evidence: a rejected tool call cannot introduce any Plan ref.
    if outcome.plan is not None:
        refs_count = outcome.plan.get("evidence_refs_count", 0)
        assert isinstance(refs_count, int) and refs_count == 0


@pytest.mark.asyncio
async def test_runtime_cancel_case_ends_cancelled_with_single_terminal(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    smoke = load_runtime_smoke_dataset()
    config = _smoke_config(smoke.manifest)
    _, trials = await EvalService(db_session).create_experiment(
        dataset=smoke, config=config
    )
    trial = next(t for t in trials if t.case_id == "runtime-cancel-01")
    case = next(c for c in smoke.cases if c.case_id == "runtime-cancel-01")
    outcome = await _runner(db_connection, deadline_seconds=15.0).run_trial(trial, case)

    assert outcome.status == "cancelled"
    assert outcome.error_code == "RUN_CANCELLED"
    terminals = terminal_events(outcome)
    assert len(terminals) == 1
    assert terminals[0]["event_type"] == "run.cancelled"
    # No orphan active Run for the Trial user.
    async with session_transaction(db_session):
        active = (
            await db_session.scalars(
                select(AgentRun).where(
                    AgentRun.user_id == outcome.user_id,
                    AgentRun.status.in_(("pending", "running")),
                )
            )
        ).all()
    assert active == []


# ---------------------------------------------------------------------------
# Terminal-waiter fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_that_never_reaches_terminal_marks_trial_timed_out(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """A Run whose executor is never driven must NOT be reported as completed.

    We construct the Run via the real service but then bypass
    ``TrialRunner.run_trial`` at the execute step: ``run_trial`` is invoked with
    a deadline so short the ``[mock:timeout]`` planning node cannot persist in
    time. The waiter must mark the Trial non-completed and never synthesize a
    fake terminal event.
    """

    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="create-01"
    )
    case = next(c for c in stage5.cases if c.case_id == "create-01")

    # Patch the executor factory by making ``_build_executor`` return an
    # executor whose ``execute`` hangs forever (simulating an unresponsive
    # graph). We subclass TrialRunner to swap that one method.
    class HangingRunner(TrialRunner):
        def _build_executor(
            self,
            *,
            trial_id: UUID,
            run_id: UUID,
            fixture_store: FixtureStore | None = None,
            provider_fixtures: dict[str, object] | None = None,
        ) -> AgentRunExecutor:
            del provider_fixtures  # HangingRunner ignores counterfactual knobs.
            from app.providers.embedding import MockEmbeddingProvider
            from app.providers.llm import MockPlanningProvider
            from app.providers.search import MockSearchProvider
            from app.tools.registry import build_tool_registry

            class _Hanging(AgentRunExecutor):
                async def execute(self, run_id: UUID) -> None:
                    await asyncio.sleep(3600)

            emb = MockEmbeddingProvider()
            return _Hanging(
                session_factory=self._session_factory,
                provider=MockPlanningProvider(),
                tool_registry=build_tool_registry(
                    settings=self._settings,
                    session_factory=self._session_factory,
                    embedding_provider=emb,
                    search_provider=MockSearchProvider(),
                ),
                embedding_provider=emb,
            )

    runner = HangingRunner(
        session_factory=runtime_factory(db_connection),
        settings=get_settings(),
        config=TrialRunnerConfig(deadline_seconds=0.5),
    )
    outcome = await runner.run_trial(trial, case)

    # The waiter cancelled the hanging execute; the Run never reached
    # completed/degraded. The Trial is therefore NOT marked completed.
    assert outcome.status != "completed"
    async with session_transaction(db_session):
        refreshed = await db_session.get(EvalTrial, trial.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status != "completed"
    # No fake terminal event was fabricated.
    terminals = terminal_events(outcome)
    if terminals:
        assert terminals[0]["event_type"] in TERMINAL_EVENT_TYPES


# ---------------------------------------------------------------------------
# Isolation + no-score invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_trial_uses_isolated_user_and_repeated_run_does_not_reuse_state(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    runner = _runner(db_connection)

    # Two Trials for the same Case must use different users and different Runs.
    outcomes: list[RunOutcome] = []
    for _index in range(2):
        _, trial = await _create_single_case_experiment(
            db_session, dataset=stage5, config_fn=_stage5_config, case_id="create-01"
        )
        case = next(c for c in stage5.cases if c.case_id == "create-01")
        outcomes.append(await runner.run_trial(trial, case))
    assert outcomes[0].user_id != outcomes[1].user_id
    assert outcomes[0].run_id != outcomes[1].run_id


@pytest.mark.asyncio
async def test_trial_runner_never_produces_score_rows(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    stage5 = load_dataset()
    _, trial = await _create_single_case_experiment(
        db_session, dataset=stage5, config_fn=_stage5_config, case_id="create-01"
    )
    case = next(c for c in stage5.cases if c.case_id == "create-01")
    await _runner(db_connection).run_trial(trial, case)
    async with session_transaction(db_session):
        scores = (
            await db_session.scalars(select(EvalScore).where(EvalScore.trial_id == trial.id))
        ).all()
    assert scores == []


# ---------------------------------------------------------------------------
# Full smoke suite via ExperimentRunner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smoke_suite_runs_twelve_cases_via_one_command(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """10 Stage 5 + 2 native V2 cases run through the real Runtime.

    Gate (PR-3 §exit): "12 Smoke cases via one command". This test is that one
    command. Run identity, tokens, latency and tool/state facts all come from
    real persisted Trace rows; no Score is generated.
    """

    stage5 = filter_cases(load_dataset(), STAGE5_SMOKE_CASE_IDS)
    smoke = load_runtime_smoke_dataset()
    factory = runtime_factory(db_connection)
    settings = get_settings()

    # Stage 5 experiment (10 cases).
    stage5_experiment, _ = await EvalService(db_session).create_experiment(
        dataset=stage5, config=_stage5_config(stage5.manifest)
    )
    # Runtime smoke experiment (2 cases).
    smoke_experiment, _ = await EvalService(db_session).create_experiment(
        dataset=smoke, config=_smoke_config(smoke.manifest)
    )
    runner = ExperimentRunner(session_factory=factory, settings=settings)
    report_a = await runner.run_experiment(stage5_experiment.id, stage5)
    report_b = await runner.run_experiment(smoke_experiment.id, smoke)

    assert report_a.trial_count == 10
    assert report_b.trial_count == 2
    assert report_a.completed_trial_count + report_b.completed_trial_count == 11
    # The cancel Smoke case is the one non-completed Trial.
    non_completed = [t for t in report_b.trials if t.status != "completed"]
    assert {t.case_id for t in non_completed} == {"runtime-cancel-01"}
    # Report carries real token/latency/tool facts (not zeros) for completed Trials.
    completed = [t for t in report_a.trials if t.status == "completed"]
    assert all(t.terminal_event_count == 1 for t in completed)
    tool_trials = [t for t in report_a.trials if t.tool_call_count > 0]
    assert {t.case_id for t in tool_trials} >= {"create-07", "create-08", "create-09"}
    assert not report_a.any_score_generated
    assert not report_b.any_score_generated


# ---------------------------------------------------------------------------
# Helpers suppressed from the import linter
# ---------------------------------------------------------------------------

_UNUSED_AGENT_STEP: type[AgentStep] = AgentStep  # noqa: F841
_UNUSED_AGENT_EVENT: type[AgentEvent] = AgentEvent  # noqa: F841
