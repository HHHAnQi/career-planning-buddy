"""Drive an Experiment's Trials through the TrialRunner.

The ExperimentRunner is a thin orchestrator: it loads the Trials already
created for an Experiment by ``EvalService.create_experiment`` (PR-2), matches
each Trial to its ``EvalCase`` by ``case_id``, marks the Experiment ``running``
and ``completed`` around the TrialRunner, and emits a compact report whose
token / tool / latency / state contents all come from real Runtime traces.

No Scores are produced -- grading is PR-4.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.harness.errors import summarize_failure_kinds
from app.models.eval import EvalExperiment, EvalTrial
from app.repositories.evals import EvalRepository
from app.services.evals import EvalService
from evals.v2.collectors.outcome import RunOutcome
from evals.v2.contracts import EvalCase
from evals.v2.dataset_loader import DatasetBundle
from evals.v2.experiment_runtime_context import ExperimentRuntimeContext
from evals.v2.stats import (
    CaseStat,
    ExperimentStat,
    compute_case_stats,
    compute_experiment_stats,
    compute_hard_gate_pass_fraction,
    gate_requirement_passed,
)
from evals.v2.trial_runner import TrialRunner


async def _bounded_gather[ItemT, ResultT](
    items: Sequence[ItemT],
    *,
    limit: int,
    operation: Callable[[ItemT], Awaitable[ResultT | None]],
) -> list[ResultT]:
    """Run async work with a hard process-local limit and stable result order."""

    semaphore = asyncio.Semaphore(limit)

    async def run_one(item: ItemT) -> ResultT | None:
        async with semaphore:
            return await operation(item)

    tasks = [asyncio.create_task(run_one(item)) for item in items]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return [result for result in results if result is not None]


@dataclass(frozen=True, slots=True)
class TrialSummary:
    trial_id: UUID
    case_id: str
    status: str
    run_status: str | None
    result_kind: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int
    error_code: str | None
    terminal_event_count: int
    tool_call_count: int
    # PR-8: counterfactual pairing. Both default to None for back-compat with
    # pre-PR-8 single-arm Trials.
    variant: str | None = None
    counterfactual_group_id: str | None = None
    # PR-9a: identifies the first attempt (index 0) per case for
    # ``first_attempt_success`` statistics. Defaults to 0 for back-compat.
    trial_index: int = 0


@dataclass(frozen=True, slots=True)
class VariantGradeDiff:
    """Per-variant grade breakdown for paired-diff reporting."""

    variant: str
    trial_id: UUID | None
    # ``grader_name -> (score, hard_gate_passed)`` for every EvalScore row
    # that landed for this variant's Trial (None when the Trial was skipped
    # or never graded).
    grades: dict[str, dict[str, float]] = field(default_factory=dict)
    tokens_in: int = 0
    tool_call_count: int = 0


@dataclass(frozen=True, slots=True)
class CounterfactualPairDiff:
    """One paired comparison inside a counterfactual group.

    ``baseline_variant`` is the canonical "control" arm (the variant name
    we treat as the reference, e.g. ``no_memory`` / ``full_context`` /
    ``tool_available`` / ``visible_evidence``). ``candidates`` holds one
    VariantGradeDiff per non-baseline variant in the group. ``case_ids``
    lists all the case_ids that contributed to this group (paired variants
    may each carry a unique case_id in the dataset).
    """

    counterfactual_group_id: str
    case_ids: list[str]
    baseline_variant: str | None
    baseline: VariantGradeDiff | None
    candidates: list[VariantGradeDiff] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    experiment_id: UUID
    experiment_status: str
    trial_count: int
    trials: list[TrialSummary] = field(default_factory=list)
    # PR-6: populated by ``run_experiment_and_grade``; left at default by the
    # grading-unaware ``run_experiment`` so existing callers (and tests) keep
    # observing ``any_score_generated is False``.
    scored_trial_count: int = 0
    hard_gate_pass_fraction: float = 0.0
    # PR-8: counterfactual paired diff. Empty when the Experiment had no
    # ``counterfactual_group_id`` Trials.
    counterfactual_pairs: list[CounterfactualPairDiff] = field(default_factory=list)
    # PR-9a: per-case statistics (variant=None trials only, keyed by case_id).
    case_stats: dict[str, CaseStat] = field(default_factory=dict)
    # PR-9a: experiment-level aggregate. None when no variant=None trials.
    experiment_stats: ExperimentStat | None = None
    failure_counts: dict[str, int] = field(default_factory=dict)

    @property
    def completed_trial_count(self) -> int:
        return sum(1 for trial in self.trials if trial.status == "completed")

    @property
    def any_score_generated(self) -> bool:
        return self.scored_trial_count > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": str(self.experiment_id),
            "experiment_status": self.experiment_status,
            "trial_count": self.trial_count,
            "completed_trial_count": self.completed_trial_count,
            "scored_trial_count": self.scored_trial_count,
            "hard_gate_pass_fraction": self.hard_gate_pass_fraction,
            "any_score_generated": self.any_score_generated,
            "trials": [asdict(trial) for trial in self.trials],
            "counterfactual_pairs": [asdict(pair) for pair in self.counterfactual_pairs],
            "case_stats": {
                case_id: asdict(stat)
                for case_id, stat in self.case_stats.items()
            },
            "experiment_stats": (
                asdict(self.experiment_stats)
                if self.experiment_stats is not None
                else None
            ),
            "failure_counts": dict(self.failure_counts),
        }


class ExperimentRunner:
    """Run every Trial of one Experiment and summarize real Trace facts."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    async def run_experiment(
        self,
        experiment_id: UUID,
        dataset: DatasetBundle,
        *,
        complete_after_trials: bool = True,
    ) -> ExperimentReport:
        cases_by_id = {case.case_id: case for case in dataset.cases}
        summaries: list[TrialSummary] = []

        # Mark the Experiment ``running`` before executing any Trial.
        async with self._session_factory() as session:
            async with session_transaction(session):
                await EvalService(session).transition_experiment(
                    experiment_id, "running"
                )

        # Build the experiment runtime context once from the persisted
        # row (Stage B-1a-lite). This is the ONLY DB read of the
        # experiment's agent_variant; the frozen dataclass is then
        # passed into TrialRunner so the trial execution layer never
        # touches the ORM for experiment metadata.
        async with self._session_factory() as session:
            experiment = await session.get(EvalExperiment, experiment_id)
            if experiment is None:
                raise RuntimeError(
                    f"experiment {experiment_id} vanished after transition"
                )
            runtime_context = ExperimentRuntimeContext(
                experiment_id=experiment.id,
                agent_variant=experiment.agent_variant,
                graph_version=experiment.graph_version,
                prompt_version=experiment.prompt_version,
                model_version=experiment.model_version,
            )

        trial_runner = TrialRunner(
            session_factory=self._session_factory,
            settings=self._settings,
            runtime_context=runtime_context,
        )
        async with self._session_factory() as session:
            trials = await EvalRepository(session).list_trials(experiment_id)
        async def execute_trial(trial: EvalTrial) -> TrialSummary | None:
            if await self._cancel_requested(experiment_id):
                return None
            case = cases_by_id.get(trial.case_id)
            if case is None:
                raise RuntimeError(
                    f"trial {trial.id} references unknown case {trial.case_id}"
                )
            outcome = await trial_runner.run_trial(
                trial,
                case,
                fixture_source_trial_id=trial.fixture_source_trial_id,
            )
            return _summarize(trial, outcome)

        summaries = await _bounded_gather(
            trials,
            limit=self._settings.eval_trial_concurrency,
            operation=execute_trial,
        )

        # Mark the Experiment terminal once no more Trial work should run.
        # Graded runs deliberately remain ``running`` here so callers never
        # observe ``completed`` before every enabled Score is persisted.
        async with self._session_factory() as session:
            service = EvalService(session)
            if await self._cancel_requested(experiment_id):
                experiment = await service.finalize_cancelled_experiment(
                    experiment_id
                )
                status = experiment.status
            elif complete_after_trials:
                await EvalService(session).transition_experiment(
                    experiment_id, "completed"
                )
                experiment = await EvalRepository(session).get_experiment(
                    experiment_id
                )
                if experiment is None:
                    raise RuntimeError(f"experiment {experiment_id} disappeared")
                status = experiment.status
            else:
                experiment = await EvalRepository(session).get_experiment(
                    experiment_id
                )
                if experiment is None:
                    raise RuntimeError(f"experiment {experiment_id} disappeared")
                status = experiment.status
        return ExperimentReport(
            experiment_id=experiment_id,
            experiment_status=status,
            trial_count=len(trials),
            trials=summaries,
            failure_counts=summarize_failure_kinds(
                [summary.error_code for summary in summaries]
            ),
        )

    async def _cancel_requested(self, experiment_id: UUID) -> bool:
        async with self._session_factory() as session:
            experiment = await EvalRepository(session).get_experiment(experiment_id)
            return bool(experiment and experiment.cancel_requested_at is not None)

    async def run_experiment_and_grade(
        self,
        experiment_id: UUID,
        dataset: DatasetBundle,
        *,
        grade: bool = True,
    ) -> ExperimentReport:
        """Run an Experiment and (optionally) grade every completed Trial.

        Stage 1 (``run_experiment``) drives the real Runtime execute per
        Trial and leaves a graded Experiment ``running``.
        Stage 2 (only when ``grade=True``) opens a fresh session, calls
        ``EvalService.grade_trial`` per completed Trial, and recomputes the
        report's score aggregates. Only after that succeeds is the Experiment
        transitioned to ``completed``. Re-grade attempts surface as
        ``AppError(code="EVAL_SCORE_ALREADY_GRADED")`` inside ``grade_trial``;
        we swallow that specific code (treating the Trial as already scored)
        but let any other exception abort the grading pass.
        """

        try:
            report = await self.run_experiment(
                experiment_id,
                dataset,
                complete_after_trials=not grade,
            )

            if not grade or report.experiment_status != "running":
                return report

            cases_by_id = {case.case_id: case for case in dataset.cases}
            scored = 0
            passed = 0
        # PR-8: map trial_id -> list of
        # (grader_name, score, gate_requirement_passed).
        # Used to assemble the counterfactual paired diffs below.
            grade_lookup: dict[UUID, list[tuple[str, float, bool]]] = {}

            for trial_summary in report.trials:
                case = cases_by_id.get(trial_summary.case_id)
                if case is None or trial_summary.status != "completed":
                    continue
            # Keep each Trial in its own Session. Reading scores starts an
            # implicit transaction; reusing that Session would make the next
            # grade_trial commit only a nested savepoint and then roll it back
            # when the shared Session closes.
                async with self._session_factory() as session:
                    repo = EvalRepository(session)
                    service = EvalService(session)
                    try:
                        await service.grade_trial(trial_summary.trial_id, case)
                    except AppError as exc:
                        if exc.code != "EVAL_SCORE_ALREADY_GRADED":
                            raise
                        # Already graded: still count it as scored below.
                    scored += 1
                    rows = await repo.list_scores(trial_summary.trial_id)
                    if rows:
                        grade_lookup[trial_summary.trial_id] = [
                            (
                                row.grader_name,
                                float(row.score) if row.score is not None else 0.0,
                                gate_requirement_passed(
                                    hard_gate=row.hard_gate,
                                    passed=row.passed,
                                ),
                            )
                            for row in rows
                        ]
                    if rows and all(
                        gate_passed
                        for _, _, gate_passed in grade_lookup[trial_summary.trial_id]
                    ):
                        passed += 1

        # PR-8: assemble counterfactual paired diffs for any Trials sharing a
        # non-NULL counterfactual_group_id. Group by group_id alone (paired
        # variants each carry their own case_id in the dataset).
            pairs: list[CounterfactualPairDiff] = []
            grouped: dict[str, list[TrialSummary]] = {}
            for summary in report.trials:
                if not summary.counterfactual_group_id:
                    continue
                grouped.setdefault(summary.counterfactual_group_id, []).append(summary)
            for group_id, group_summaries in grouped.items():
                pairs.append(
                    _build_counterfactual_pair(group_summaries, grade_lookup, group_id)
                )

        # PR-9a: per-case + per-experiment statistics. Variant-tagged trials
        # are excluded from the rollup (see stats.compute_case_stats).
            case_stats = compute_case_stats(list(report.trials), grade_lookup)
            experiment_stats = compute_experiment_stats(case_stats)

            async with self._session_factory() as session:
                await EvalService(session).transition_experiment(
                    experiment_id, "completed"
                )

            return ExperimentReport(
                experiment_id=report.experiment_id,
                experiment_status="completed",
                trial_count=report.trial_count,
                trials=list(report.trials),
                scored_trial_count=scored,
                hard_gate_pass_fraction=compute_hard_gate_pass_fraction(
                    passed_count=passed,
                    summaries=list(report.trials),
                ),
                counterfactual_pairs=pairs,
                case_stats=case_stats,
                experiment_stats=experiment_stats,
                failure_counts=summarize_failure_kinds(
                    [summary.error_code for summary in report.trials]
                ),
            )
        except Exception:
            async with self._session_factory() as session:
                async with session_transaction(session):
                    try:
                        await EvalService(session).transition_experiment(
                            experiment_id, "failed"
                        )
                    except Exception:
                        # The transition itself may be illegal if the
                        # Experiment never reached ``running``; never mask
                        # the original failure.
                        pass
            raise


def _summarize(trial: EvalTrial, outcome: RunOutcome) -> TrialSummary:
    from evals.v2.collectors.outcome import terminal_event_count

    # A Trial is "completed" for reporting purposes when the Run reached a
    # legitimate terminal outcome (completed/degraded). cancelled/failed Runs
    # leave the Trial in a non-completed status. The ``trial`` ORM object may
    # be stale (TrialRunner attached the outcome from another session), so we
    # derive report status from the outcome rather than the ORM row.
    report_status = "completed" if outcome.status in {"completed", "degraded"} else (
        "cancelled" if outcome.status == "cancelled" else "failed"
    )
    return TrialSummary(
        trial_id=trial.id,
        case_id=trial.case_id,
        status=report_status,
        run_status=outcome.status,
        result_kind=outcome.result_kind,
        tokens_in=outcome.total_tokens_in,
        tokens_out=outcome.total_tokens_out,
        latency_ms=outcome.total_latency_ms,
        error_code=outcome.error_code,
        terminal_event_count=terminal_event_count(outcome),
        tool_call_count=len(outcome.tool_calls),
        variant=trial.variant,
        counterfactual_group_id=trial.counterfactual_group_id,
        trial_index=trial.trial_index,
    )


_BASELINE_VARIANT_HINTS: tuple[str, ...] = (
    "no_memory",
    "full_context",
    "tool_available",
    "visible_evidence",
    "baseline",
)


def _is_baseline_variant(variant: str | None) -> bool:
    if not variant:
        return True
    return any(hint in variant for hint in _BASELINE_VARIANT_HINTS)


def _build_counterfactual_pair(
    summaries: list[TrialSummary],
    grade_lookup: dict[UUID, list[tuple[str, float, bool]]],
    group_id: str,
) -> CounterfactualPairDiff:
    """Assemble one paired diff row from the Trial summaries + grades.

    The grouping is by ``counterfactual_group_id`` alone (paired variants
    each carry their own case_id in the dataset, so we cannot key on
    case_id).
    """

    def _grades_for(summary: TrialSummary) -> VariantGradeDiff:
        rows = grade_lookup.get(summary.trial_id, [])
        grades = {
            grader: {
                "score": float(score) if score is not None else 0.0,
                "hard_gate": 1.0 if passed else 0.0,
            }
            for grader, score, passed in rows
        }
        return VariantGradeDiff(
            variant=summary.variant or "",
            trial_id=summary.trial_id,
            grades=grades,
            tokens_in=summary.tokens_in,
            tool_call_count=summary.tool_call_count,
        )

    baseline_summary = next(
        (s for s in summaries if _is_baseline_variant(s.variant)), None
    )
    baseline = _grades_for(baseline_summary) if baseline_summary else None
    candidates = [
        _grades_for(s)
        for s in summaries
        if not _is_baseline_variant(s.variant)
        and s.counterfactual_group_id == group_id
    ]
    case_ids = sorted({s.case_id for s in summaries if s.counterfactual_group_id == group_id})
    return CounterfactualPairDiff(
        counterfactual_group_id=group_id,
        case_ids=case_ids,
        baseline_variant=baseline_summary.variant if baseline_summary else None,
        baseline=baseline,
        candidates=candidates,
    )


# ``EvalExperiment`` / ``EvalCase`` are re-exported as type aliases for callers
# that build typed reports on top of ``ExperimentRunner``.
_EXPERIMENT_GUARD: type[EvalExperiment] = EvalExperiment  # noqa: F841
_CASE_GUARD: type[EvalCase] = EvalCase  # noqa: F841
