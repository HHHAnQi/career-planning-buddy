"""PR-6 batch driver tests.

Cover:
1. ``ExperimentRunner.run_experiment_and_grade`` produces scores per completed
   Trial through a real fixture-provider Runtime pass; the report's
   ``scored_trial_count`` / ``any_score_generated`` / per-trial Score rows all
   reflect grading.
2. ``ExperimentReport.to_dict`` emits a JSON-serializable projection whose
   ``any_score_generated`` flag follows the injected ``scored_trial_count``.
3. ``python -m evals.v2 --help`` (subprocess) exits 0 -- the module imports
   cleanly without exercising the Runtime end-to-end.
4. Re-grading a Trial inside the driver is idempotent (already-graded Trials
   are skipped without aborting the run).

These require live PostgreSQL via ``db_connection``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
)

from app.core.config import get_settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.contracts import DatasetManifest, EvalCase, ExperimentCreate
from evals.v2.dataset_loader import filter_cases, load_dataset
from evals.v2.experiment_runner import (
    ExperimentReport,
    ExperimentRunner,
    TrialSummary,
    _bounded_gather,
)
from tests.test_agent_runtime import runtime_factory

DOMAINS = {"task", "behavioral", "tool", "model", "system", "safety"}


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
# 1. run_experiment_and_grade end-to-end (minimal Stage 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_experiment_and_grade_smoke_produces_scores(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """Driver runs a real Stage-5 Trial and grades it."""

    stage5 = filter_cases(load_dataset(), ["create-01"])
    factory = runtime_factory(db_connection)
    settings = get_settings()

    stage5_experiment, _ = await EvalService(db_session).create_experiment(
        dataset=stage5, config=_stage5_config(stage5.manifest)
    )

    runner = ExperimentRunner(session_factory=factory, settings=settings)
    report = await runner.run_experiment_and_grade(
        stage5_experiment.id, stage5, grade=True
    )

    # Completed Trial counted and graded.
    assert report.completed_trial_count == 1
    assert report.scored_trial_count > 0
    assert report.any_score_generated is True

    # The completed Stage-5 Trial carries Score rows across all domains.
    async with session_transaction(db_session):
        repo = EvalRepository(db_session)
        for trial in report.trials:
            if trial.status != "completed":
                continue
            scores = await repo.list_scores(trial.trial_id)
            scored_domains = {score.domain for score in scores}
            assert DOMAINS.issubset(scored_domains), (
                f"trial {trial.trial_id} missing domains: {DOMAINS - scored_domains}"
            )
            expected_gate_passed = all(
                not score.hard_gate or score.passed is True for score in scores
            )
            assert report.hard_gate_pass_fraction == float(expected_gate_passed)

    # Rebuilding from persisted rows must use exactly the same gate semantics.
    # A report request starts in a fresh Session. Reusing ``db_session`` here
    # would retain the pre-run transaction snapshot and observe the original
    # draft Experiment instead of the rows committed by the runner Sessions.
    async with factory() as report_session:
        rebuilt = await EvalService(report_session).build_report(
            stage5_experiment.id, stage5
        )
    assert rebuilt.hard_gate_pass_fraction == report.hard_gate_pass_fraction
    assert rebuilt.case_stats == report.case_stats


@pytest.mark.asyncio
async def test_grading_failure_keeps_experiment_from_false_completed(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grader failure converges the Experiment to ``failed``.

    The injected grader also observes the persisted status before raising,
    proving the control plane remains ``running`` throughout grading.
    """

    stage5 = filter_cases(load_dataset(), ["create-01"])
    factory = runtime_factory(db_connection)
    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=stage5, config=_stage5_config(stage5.manifest)
    )

    async def fail_while_running(
        service: EvalService,
        trial_id: UUID,
        case: EvalCase,
    ) -> None:
        del service, trial_id, case
        async with factory() as status_session:
            current = await EvalRepository(status_session).get_experiment(
                experiment.id
            )
        assert current is not None
        assert current.status == "running"
        raise RuntimeError("injected grader failure")

    monkeypatch.setattr(EvalService, "grade_trial", fail_while_running)
    runner = ExperimentRunner(session_factory=factory, settings=get_settings())

    with pytest.raises(RuntimeError, match="injected grader failure"):
        await runner.run_experiment_and_grade(experiment.id, stage5, grade=True)

    async with factory() as session:
        persisted = await EvalRepository(session).get_experiment(experiment.id)
    assert persisted is not None
    assert persisted.status == "failed"


@pytest.mark.asyncio
async def test_run_experiment_without_grade_keeps_scores_unset(
    db_connection: AsyncConnection,
    db_session: AsyncSession,
) -> None:
    """``grade=False`` repeats the legacy PR-3 contract: no scores attached.

    Also revisits the idempotency contract from a different angle: after a
    non-grading run, the same Experiment cannot be re-driven (its
    ``completed`` status blocks the ``running`` transition), and grading the
    Trial once via EvalService then re-invoking grade_trial surfaces
    EVAL_SCORE_ALREADY_GRADED -- the same AppError the driver would swallow.
    """

    stage5 = filter_cases(load_dataset(), ["create-01"])
    factory = runtime_factory(db_connection)
    settings = get_settings()

    experiment, _ = await EvalService(db_session).create_experiment(
        dataset=stage5, config=_stage5_config(stage5.manifest)
    )
    runner = ExperimentRunner(session_factory=factory, settings=settings)
    report = await runner.run_experiment_and_grade(
        experiment.id, stage5, grade=False
    )
    assert report.scored_trial_count == 0
    assert report.any_score_generated is False

    # Manually grade the completed Trial, then re-grade to confirm the
    # EVAL_SCORE_ALREADY_GRADED mapping the driver relies on.
    case = stage5.cases[0]
    trial = next(t for t in report.trials if t.status == "completed")
    await EvalService(db_session).grade_trial(trial.trial_id, case)
    with pytest.raises(AppError) as error:
        await EvalService(db_session).grade_trial(trial.trial_id, case)
    assert error.value.code == "EVAL_SCORE_ALREADY_GRADED"


# ---------------------------------------------------------------------------
# 3. ExperimentReport.to_dict
# ---------------------------------------------------------------------------


def test_experiment_report_to_dict_serializes_and_derives_score_flag() -> None:
    """``to_dict`` produces a JSON-safe payload with any_score_generated derived
    from scored_trial_count (not a hardcoded False)."""

    summary = TrialSummary(
        trial_id=uuid4(),
        case_id="x",
        status="completed",
        run_status="completed",
        result_kind="plan",
        tokens_in=10,
        tokens_out=20,
        latency_ms=300,
        error_code=None,
        terminal_event_count=1,
        tool_call_count=2,
    )
    empty = ExperimentReport(
        experiment_id=uuid4(),
        experiment_status="completed",
        trial_count=1,
        trials=[summary],
    )
    assert empty.any_score_generated is False
    payload_empty = empty.to_dict()
    assert payload_empty["any_score_generated"] is False
    # Ensure the payload is JSON-serializable (no UUID/dataclass leak).
    json.dumps(payload_empty, default=str)

    graded = ExperimentReport(
        experiment_id=uuid4(),
        experiment_status="completed",
        trial_count=1,
        trials=[summary],
        scored_trial_count=1,
        hard_gate_pass_fraction=1.0,
    )
    assert graded.any_score_generated is True
    payload_graded = graded.to_dict()
    assert payload_graded["any_score_generated"] is True
    assert payload_graded["hard_gate_pass_fraction"] == 1.0
    assert payload_graded["scored_trial_count"] == 1


# ---------------------------------------------------------------------------
# 4. CLI smoke: subprocess ``--help`` imports succeed
# ---------------------------------------------------------------------------


def test_cli_help_exits_zero() -> None:
    """``python -m evals.v2 --help`` proves the module imports cleanly and the
    CLI is wired up. We do NOT execute a real run here (that needs live LLM
    + authenticated database credentials)."""

    result = subprocess.run(
        [sys.executable, "-m", "evals.v2", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"CLI --help failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Eval Harness V2" in result.stdout or "usage:" in result.stdout.lower()


def test_cli_hard_gate_contract_requires_complete_scored_pass() -> None:
    from evals.v2.__main__ import _all_hard_gates_passed

    passing = {
        "trial_count": 2,
        "completed_trial_count": 2,
        "scored_trial_count": 2,
        "hard_gate_pass_fraction": 1.0,
    }
    assert _all_hard_gates_passed(passing) is True

    for field, value in (
        ("completed_trial_count", 1),
        ("scored_trial_count", 1),
        ("hard_gate_pass_fraction", 0.5),
    ):
        failing = {**passing, field: value}
        assert _all_hard_gates_passed(failing) is False


@pytest.mark.asyncio
async def test_bounded_gather_enforces_limit_and_preserves_order() -> None:
    active = 0
    max_active = 0

    async def operation(value: int) -> int:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return value * 2

    results = await _bounded_gather(
        list(range(6)),
        limit=2,
        operation=operation,
    )

    assert max_active == 2
    assert results == [0, 2, 4, 6, 8, 10]
