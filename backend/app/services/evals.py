"""Use cases and legal state transitions for the Eval V2 control plane."""

from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.harness.provider_calls.repository import ProviderCallRepository
from app.models.agent_run import AgentRun
from app.models.eval import (
    EvalEvidenceItem,
    EvalExperiment,
    EvalPairwiseJudgeResult,
    EvalScore,
    EvalTrial,
    EvalTrialPair,
)
from app.repositories.evals import EvalRepository
from evals.v2.collectors.outcome import outcome_from_snapshot
from evals.v2.contracts import (
    EvalCase,
    ExperimentCreate,
    GradeResult,
    canonical_sha256,
)
from evals.v2.dataset_loader import DatasetBundle
from evals.v2.graders.base import (
    EvidenceItem,
    authorize,
)
from evals.v2.graders.registry import grade_all
from evals.v2.judge import PairwiseJudge
from evals.v2.pairwise import (
    JUDGE_ALLOWED_KINDS,
    PositionVariant,
    build_judge_input,
    build_pair,
)

if TYPE_CHECKING:
    from evals.v2.experiment_runner import ExperimentReport

EXPERIMENT_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"running", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class EvalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._evals = EvalRepository(session)

    async def create_experiment(
        self,
        *,
        dataset: DatasetBundle,
        config: ExperimentCreate,
        run_type: str = "evaluation",
        fixture_source_experiment_id: UUID | None = None,
    ) -> tuple[EvalExperiment, list[EvalTrial]]:
        manifest = dataset.manifest
        if (
            config.dataset_id != manifest.dataset_id
            or config.dataset_version != manifest.dataset_version
            or config.dataset_hash != manifest.source_sha256
        ):
            raise AppError(
                code="EVAL_DATASET_VERSION_MISMATCH",
                message="Experiment dataset identity/hash does not match the loaded manifest",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        if run_type not in {
            "evaluation",
            "fixture_replay",
            "live_rerun",
            "candidate_backtest",
        }:
            raise AppError(
                code="EVAL_RUN_TYPE_INVALID",
                message="Eval Trial run_type is invalid",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        if run_type == "fixture_replay":
            if fixture_source_experiment_id is None:
                raise AppError(
                    code="EVAL_FIXTURE_SOURCE_REQUIRED",
                    message="fixture_replay requires fixture_source_experiment_id",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
            if config.execution_mode != "fixture_provider":
                raise AppError(
                    code="EVAL_FIXTURE_PROVIDER_REQUIRED",
                    message="fixture_replay requires execution_mode=fixture_provider",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                )
        elif fixture_source_experiment_id is not None:
            raise AppError(
                code="EVAL_FIXTURE_SOURCE_INVALID",
                message="fixture_source_experiment_id is only valid for fixture_replay",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        # PR-9b: refuse execution_mode=live_provider when the global
        # Settings.llm_provider is still mock — the TrialRunner would
        # otherwise silently degrade a "live" experiment to mock semantics
        # and reports would mislabel the run as real-provider.
        from app.core.config import get_settings as _get_settings

        _settings = _get_settings()
        if (
            config.execution_mode == "live_provider"
            and _settings.llm_provider == "mock"
        ):
            raise AppError(
                code="EVAL_PROVIDER_MODE_INVALID",
                message=(
                    "execution_mode=live_provider requires Settings.llm_provider "
                    "!= 'mock' (got 'mock'). Set LLM_PROVIDER=openai_compatible "
                    "and the matching LLM_API_KEY/LLM_BASE_URL."
                ),
                status_code=HTTPStatus.CONFLICT,
            )
        frozen_hash = canonical_sha256(config.frozen_config())
        async with session_transaction(self._session):
            fixture_sources: dict[tuple[str, int, str | None], EvalTrial] = {}
            if fixture_source_experiment_id is not None:
                source_experiment = await self._evals.get_experiment(
                    fixture_source_experiment_id
                )
                if source_experiment is None:
                    raise AppError(
                        code="EVAL_FIXTURE_SOURCE_NOT_FOUND",
                        message="Fixture source Experiment was not found",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                if source_experiment.status != "completed":
                    raise AppError(
                        code="EVAL_FIXTURE_SOURCE_NOT_COMPLETED",
                        message="Fixture source Experiment must be completed",
                        status_code=HTTPStatus.CONFLICT,
                    )
                if source_experiment.execution_mode != "fixture_provider":
                    raise AppError(
                        code="EVAL_FIXTURE_SOURCE_MODE_INVALID",
                        message="Fixture source Experiment must use fixture_provider",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                if (
                    source_experiment.dataset_id,
                    source_experiment.dataset_version,
                    source_experiment.dataset_hash,
                ) != (
                    config.dataset_id,
                    config.dataset_version,
                    config.dataset_hash,
                ):
                    raise AppError(
                        code="EVAL_FIXTURE_SOURCE_DATASET_MISMATCH",
                        message="Fixture source and replay must use the same frozen dataset",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                source_trials = await self._evals.list_trials(
                    fixture_source_experiment_id
                )
                fixture_sources = {
                    (trial.case_id, trial.trial_index, trial.variant): trial
                    for trial in source_trials
                }
            if config.baseline_experiment_id is not None:
                baseline = await self._evals.get_experiment(config.baseline_experiment_id)
                if baseline is None:
                    raise AppError(
                        code="EVAL_BASELINE_NOT_FOUND",
                        message="Baseline Experiment was not found",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                if baseline.variant_role != "baseline":
                    raise AppError(
                        code="EVAL_BASELINE_ROLE_INVALID",
                        message="Referenced Experiment is not a baseline",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                if (baseline.dataset_id, baseline.dataset_version) != (
                    config.dataset_id,
                    config.dataset_version,
                ):
                    raise AppError(
                        code="EVAL_BASELINE_DATASET_MISMATCH",
                        message="Candidate and Baseline must use the same versioned dataset",
                        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
            experiment = EvalExperiment(
                id=config.experiment_id,
                dataset_id=config.dataset_id,
                dataset_version=config.dataset_version,
                dataset_hash=config.dataset_hash,
                git_commit=config.git_commit,
                graph_version=config.graph_version,
                feature_stage=config.feature_stage,
                prompt_version=config.prompt_version,
                model_version=config.model_version,
                tool_version=config.tool_version,
                context_version=config.context_version,
                memory_version=config.memory_version,
                search_version=config.search_version,
                eval_harness_version=config.eval_harness_version,
                frozen_config_hash=frozen_hash,
                execution_mode=config.execution_mode,
                variant_role=config.variant_role,
                baseline_experiment_id=config.baseline_experiment_id,
                trial_count=config.trial_count,
                agent_variant=config.agent_variant,
                status="draft",
            )
            await self._evals.create_experiment(experiment)
            trials: list[EvalTrial] = []
            provider_calls = ProviderCallRepository(self._session)
            for case in dataset.cases:
                for trial_index in range(config.trial_count):
                    source_trial = fixture_sources.get(
                        (case.case_id, trial_index, case.variant)
                    )
                    if fixture_source_experiment_id is not None:
                        if (
                            source_trial is None
                            or source_trial.status != "completed"
                            or source_trial.run_type == "fixture_replay"
                            or source_trial.case_fixture_hash != case.fixture_hash
                        ):
                            raise AppError(
                                code="EVAL_FIXTURE_SOURCE_TRIAL_INVALID",
                                message=(
                                    "Fixture source is missing a completed non-replay Trial "
                                    f"for case={case.case_id}, index={trial_index}, "
                                    f"variant={case.variant}"
                                ),
                                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                            )
                        bundles = await provider_calls.list_bundles_for_trial(
                            source_trial.id
                        )
                        if len(bundles) != 1:
                            raise AppError(
                                code="EVAL_FIXTURE_SOURCE_BUNDLE_INVALID",
                                message="Each fixture source Trial must own exactly one bundle",
                                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                            )
                    trials.append(EvalTrial(
                    experiment_id=experiment.id,
                    case_id=case.case_id,
                    case_fixture_hash=case.fixture_hash,
                    trial_index=trial_index,
                    # PR-8: paired variants of one counterfactual_group share
                    # a deterministic base seed derived from the group id so
                    # every variant lands on the same Mock-provider LLM
                    # trajectory. Pre-PR-8 cases (group_id is NULL) keep the
                    # legacy fixture_hash-derived seed.
                    seed=_trial_seed(
                        case.counterfactual_group_id
                        and canonical_sha256(case.counterfactual_group_id)
                        or case.fixture_hash,
                        trial_index,
                    ),
                    variant=case.variant,
                    counterfactual_group_id=case.counterfactual_group_id,
                    run_type=run_type,
                    fixture_source_trial_id=(
                        source_trial.id if source_trial is not None else None
                    ),
                    status="pending",
                    ))
            await self._evals.create_trials(trials)
        return experiment, trials

    async def transition_experiment(
        self, experiment_id: UUID, target_status: str
    ) -> EvalExperiment:
        async with session_transaction(self._session):
            experiment = await self._evals.get_experiment(experiment_id, for_update=True)
            if experiment is None:
                raise AppError(
                    code="EVAL_EXPERIMENT_NOT_FOUND",
                    message="Eval Experiment was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if target_status not in EXPERIMENT_TRANSITIONS[experiment.status]:
                raise AppError(
                    code="EVAL_EXPERIMENT_TRANSITION_INVALID",
                    message=f"Illegal Experiment transition {experiment.status} -> {target_status}",
                    status_code=HTTPStatus.CONFLICT,
                )
            now = datetime.now(UTC)
            if target_status == "running":
                if self._frozen_hash(experiment) != experiment.frozen_config_hash:
                    raise AppError(
                        code="EVAL_EXPERIMENT_CONFIG_CHANGED",
                        message="Experiment frozen configuration hash no longer matches",
                        status_code=HTTPStatus.CONFLICT,
                    )
                experiment.started_at = now
            if target_status == "completed":
                remaining = await self._evals.count_nonterminal_trials(experiment.id)
                if remaining:
                    raise AppError(
                        code="EVAL_EXPERIMENT_TRIALS_INCOMPLETE",
                        message="Experiment cannot complete while Trials are pending or running",
                        status_code=HTTPStatus.CONFLICT,
                    )
            if target_status in {"completed", "failed", "cancelled"}:
                experiment.finished_at = now
            experiment.status = target_status
            await self._session.flush()
            return experiment

    async def add_grade(self, trial_id: UUID, result: GradeResult) -> EvalScore:
        async with session_transaction(self._session):
            trial = await self._evals.get_trial(trial_id, for_update=True)
            if trial is None:
                raise AppError(
                    code="EVAL_TRIAL_NOT_FOUND",
                    message="Eval Trial was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if trial.status != "completed" or trial.run_id is None:
                raise AppError(
                    code="EVAL_TRIAL_NOT_GRADEABLE",
                    message="Only a completed Trial backed by a real Agent Run can be graded",
                    status_code=HTTPStatus.CONFLICT,
                )
            score = EvalScore(
                trial_id=trial.id,
                grader_name=result.grader_name,
                grader_version=result.grader_version,
                domain=result.domain,
                metric_type=result.metric_type,
                score=result.score,
                passed=result.passed,
                categorical_value=result.categorical_value,
                hard_gate=result.hard_gate,
                threshold=result.threshold,
                evidence_item_ids=[str(item_id) for item_id in result.evidence_item_ids],
                evidence_json=result.evidence,
                rationale=result.rationale,
            )
            return await self._evals.create_score(score)

    async def attach_evidence(
        self, trial_id: UUID, items: list[EvidenceItem]
    ) -> list[EvalEvidenceItem]:
        """Persist a fresh evidence catalog for one Trial.

        This helper exists for pre-run fixture construction. Once a Trial is
        terminal its evidence catalog is immutable and can only have been
        written atomically by ``TrialRunner._finalize_trial``.
        """

        async with session_transaction(self._session):
            trial = await self._evals.get_trial(trial_id, for_update=True)
            if trial is None:
                raise AppError(
                    code="EVAL_TRIAL_NOT_FOUND",
                    message="Eval Trial was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if trial.status not in {"pending", "running"}:
                raise AppError(
                    code="EVAL_EVIDENCE_FROZEN",
                    message="Terminal Trial evidence cannot be replaced",
                    status_code=HTTPStatus.CONFLICT,
                )
            await self._evals.delete_evidence_for_trial(trial_id)
            rows = [
                EvalEvidenceItem(
                    trial_id=trial_id,
                    kind=item.kind.value,
                    source_type=item.source_type,
                    source_id=item.source_id,
                    content_hash=item.content_hash,
                    projection_json=item.projection,
                    sensitivity=item.sensitivity,
                )
                for item in items
            ]
            return await self._evals.create_evidence_items(rows)

    async def grade_trial(
        self,
        trial_id: UUID,
        case: EvalCase,
    ) -> list[GradeResult]:
        """Grade one Trial exclusively from its terminal frozen artifacts.

        Steps:
        1. Load the Trial; require ``completed`` + run_id (same gate as
           ``add_grade``: a non-completed Trial cannot be graded).
        2. Verify the supplied Case is exactly the fixture frozen on Trial.
        3. Rebuild ``RunOutcome`` from ``outcome_snapshot_json`` and load the
           Evidence rows frozen in the same terminal transaction.
        4. ``grade_all`` (Registry) -> ``GradeResult`` list, persisted via
           ``add_grade`` per row.

        Returns the list of ``GradeResult`` objects. DB-level score uniqueness
        on (trial_id, grader_name, grader_version) prevents silent re-grading;
        callers that want to re-grade after content change must first clear
        scores at the Repository layer.
        """

        async with session_transaction(self._session):
            trial = await self._evals.get_trial(trial_id, for_update=True)
            if trial is None:
                raise AppError(
                    code="EVAL_TRIAL_NOT_FOUND",
                    message="Eval Trial was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if trial.status != "completed" or trial.run_id is None:
                raise AppError(
                    code="EVAL_TRIAL_NOT_GRADEABLE",
                    message="Only a completed Trial backed by a real Agent Run can be graded",
                    status_code=HTTPStatus.CONFLICT,
                )
            if case.case_id != trial.case_id or case.fixture_hash != trial.case_fixture_hash:
                raise AppError(
                    code="EVAL_CASE_FIXTURE_MISMATCH",
                    message="The grading Case does not match the Trial's frozen fixture",
                    status_code=HTTPStatus.CONFLICT,
                )
            if trial.outcome_snapshot_json is None or trial.transcript_hash is None:
                raise AppError(
                    code="EVAL_TRIAL_SNAPSHOT_INVALID",
                    message="Completed Trial is missing its frozen outcome snapshot",
                    status_code=HTTPStatus.CONFLICT,
                )
            try:
                outcome = outcome_from_snapshot(
                    trial.outcome_snapshot_json,
                    transcript_hash=trial.transcript_hash,
                )
            except ValueError as exc:
                raise AppError(
                    code="EVAL_TRIAL_SNAPSHOT_INVALID",
                    message="Completed Trial has an invalid frozen outcome snapshot",
                    status_code=HTTPStatus.CONFLICT,
                ) from exc
            rows = await self._evals.list_evidence_items(trial_id)
            if not rows:
                raise AppError(
                    code="EVAL_TRIAL_EVIDENCE_MISSING",
                    message="Completed Trial is missing its frozen evidence catalog",
                    status_code=HTTPStatus.CONFLICT,
                )
            from evals.v2.arm_invariants import check_post_trial
            from evals.v2.graders.base import EvidenceKind

            items = [
                EvidenceItem(
                    id=row.id,
                    trial_id=row.trial_id,
                    kind=EvidenceKind(row.kind),
                    source_type=row.source_type,
                    source_id=row.source_id or "",
                    content_hash=row.content_hash,
                    projection=row.projection_json,
                    sensitivity=row.sensitivity,
                )
                for row in rows
            ]
            # Arm-configuration invariant: a misconfigured arm must fail
            # grading loudly instead of producing a wrong-but-plausible
            # conclusion (root cause of the first-round "fake bare" arm).
            from evals.v2.graders.base import EvidenceKind as _EK

            tool_names = [
                str(item.projection.get("tool_name"))
                for item in items
                if item.kind == _EK.TOOL_CALL_PROJECTION
                and isinstance(item.projection, dict)
                and item.projection.get("tool_name")
            ]
            experiment = await self._evals.get_experiment(trial.experiment_id)
            run = await self._session.get(AgentRun, trial.run_id)
            violations = check_post_trial(
                experiment.agent_variant if experiment else None,
                dict(run.config_snapshot_json or {}) if run is not None else {},
                tool_names,
            )
            if violations:
                raise AppError(
                    code="EVAL_ARM_INVARIANT_VIOLATED",
                    message="; ".join(
                        f"{v.check}: {v.detail}" for v in violations
                    ),
                    status_code=HTTPStatus.CONFLICT,
                )
            results = await grade_all(
                trial_id=trial_id,
                outcome=outcome,
                evidence_items=items,
                expected=case,
            )
            for result in results:
                try:
                    await self.add_grade(trial_id, result)
                except IntegrityError as exc:
                    raise AppError(
                        code="EVAL_SCORE_ALREADY_GRADED",
                        message="Trial has already been graded for this grader/version.",
                        status_code=HTTPStatus.CONFLICT,
                    ) from exc
            return results

    async def set_cancel_requested(self, experiment_id: UUID) -> tuple[bool, datetime | None]:
        """Stamp ``cancel_requested_at`` on a running Experiment.

        Returns ``(requested, cancel_requested_at)``. ``requested=False``
        means the Experiment is already terminal (completed/failed/
        cancelled) and the request is a no-op idempotent. ``requested=True``
        means we just stamped the timestamp (or it was already there).
        Never promotes the Experiment to ``cancelled`` status — the
        background executor observes the timestamp and transitions when
        every in-flight Trial reaches a terminal state.
        """

        from datetime import UTC
        from datetime import datetime as _dt

        async with session_transaction(self._session):
            experiment = await self._evals.get_experiment(experiment_id, for_update=True)
            if experiment is None:
                raise AppError(
                    code="EVAL_EXPERIMENT_NOT_FOUND",
                    message="Eval Experiment was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if experiment.status in {"completed", "failed", "cancelled"}:
                return False, experiment.cancel_requested_at
            if experiment.cancel_requested_at is None:
                experiment.cancel_requested_at = _dt.now(UTC)
            return True, experiment.cancel_requested_at

    async def finalize_cancelled_experiment(
        self, experiment_id: UUID
    ) -> EvalExperiment:
        """Idempotently converge an operator-cancelled Eval to terminal state."""

        async with session_transaction(self._session):
            experiment = await self._evals.get_experiment(
                experiment_id, for_update=True
            )
            if experiment is None:
                raise AppError(
                    code="EVAL_EXPERIMENT_NOT_FOUND",
                    message="Eval Experiment was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            if experiment.status in {"completed", "failed", "cancelled"}:
                return experiment

            now = datetime.now(UTC)
            await self._evals.cancel_nonterminal_trials(
                experiment.id,
                finished_at=now,
                error_code="USER_REQUESTED_CANCEL",
                error_message="Eval experiment was cancelled by a developer",
            )
            experiment.status = "cancelled"
            experiment.finished_at = now
            await self._session.flush()
            return experiment

    async def regenerate_report(
        self, experiment_id: UUID, dataset: DatasetBundle
    ) -> "ExperimentReport":
        """Pure-aggregate rebuild of the report; no Runtime / Trial side effects.

        Bumps ``report_revision`` only when the computed report content
        hash changes. Always returns the freshly-built ExperimentReport.
        """

        from evals.v2.contracts import canonical_sha256 as _canon

        report = await self.build_report(experiment_id, dataset)
        async with session_transaction(self._session):
            experiment = await self._evals.get_experiment(
                experiment_id, for_update=True
            )
            if experiment is None:
                raise AppError(
                    code="EVAL_EXPERIMENT_NOT_FOUND",
                    message="Eval Experiment was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            content = report.to_dict()
            # Content hash excludes revision/hash themselves so a bump
            # does not feed back into the hash.
            content_for_hash = {
                k: v for k, v in content.items()
                if k not in {"revision"}
            }
            new_hash = _canon(content_for_hash)
            if experiment.report_content_hash != new_hash:
                experiment.report_content_hash = new_hash
                experiment.report_revision = int(experiment.report_revision) + 1
        return report

    async def build_report(
        self,
        experiment_id: UUID,
        dataset: DatasetBundle,
    ) -> "ExperimentReport":
        """Reconstruct an ``ExperimentReport`` from DB state without re-running.

        Used by the HTTP GET report endpoint (and any future CLI ``--report``).
        Reads ``EvalExperiment`` + ``EvalTrial`` rows + ``EvalScore`` rows for
        every completed Trial and aggregates them into the same frozen
        ``ExperimentReport`` shape that
        ``ExperimentRunner.run_experiment_and_grade`` returns.
        """

        from evals.v2.experiment_runner import (
            CounterfactualPairDiff,
            ExperimentReport,
            TrialSummary,
            VariantGradeDiff,
            _build_counterfactual_pair,
        )
        from evals.v2.stats import (
            compute_hard_gate_pass_fraction,
            gate_requirement_passed,
        )

        async with session_transaction(self._session):
            experiment = await self._evals.get_experiment(experiment_id)
            if experiment is None:
                raise AppError(
                    code="EVAL_EXPERIMENT_NOT_FOUND",
                    message="Eval Experiment was not found",
                    status_code=HTTPStatus.NOT_FOUND,
                )
            trials = await self._evals.list_trials(experiment_id)
            cases_by_id = {case.case_id: case for case in dataset.cases}
            summaries: list[TrialSummary] = []
            scored = 0
            passed = 0
            grade_lookup: dict[UUID, list[tuple[str, float, bool]]] = {}
            for trial in trials:
                case = cases_by_id.get(trial.case_id)
                # Cases outside the provided dataset snapshot still get a
                # TrialSummary -- the report must enumerate every persisted
                # Trial even if the caller's dataset bundle is stale.
                _ = case
                summaries.append(_trial_summary_from_snapshot(trial))
                if trial.status == "completed":
                    scores = await self._evals.list_scores(trial.id)
                    if scores:
                        scored += 1
                        grade_lookup[trial.id] = [
                            (
                                row.grader_name,
                                float(row.score) if row.score is not None else 0.0,
                                gate_requirement_passed(
                                    hard_gate=row.hard_gate,
                                    passed=row.passed,
                                ),
                            )
                            for row in scores
                        ]
                        if all(
                            gate_passed
                            for _, _, gate_passed in grade_lookup[trial.id]
                        ):
                            passed += 1
            # PR-8: build counterfactual paired diffs (mirror of
            # ExperimentRunner.run_experiment_and_grade's grouping).
            grouped: dict[str, list[TrialSummary]] = {}
            for summary in summaries:
                if not summary.counterfactual_group_id:
                    continue
                grouped.setdefault(summary.counterfactual_group_id, []).append(summary)
            pairs: list[CounterfactualPairDiff] = [
                _build_counterfactual_pair(
                    group_summaries, grade_lookup, group_id
                )
                for group_id, group_summaries in grouped.items()
            ]
            _ = VariantGradeDiff  # re-export for future typed callers
            # PR-9a: compute per-case + per-experiment stats. Re-uses the
            # grade_lookup already built for counterfactual pairs so the
            # hard-gate verdicts stay consistent across the report shape.
            from app.harness.errors import summarize_failure_kinds
            from evals.v2.stats import (
                compute_case_stats as _cf_stats,
            )
            from evals.v2.stats import (
                compute_experiment_stats as _cf_exp_stats,
            )
            case_stats = _cf_stats(summaries, grade_lookup)
            experiment_stats = _cf_exp_stats(case_stats)
            return ExperimentReport(
                experiment_id=experiment.id,
                experiment_status=experiment.status,
                trial_count=len(trials),
                trials=summaries,
                scored_trial_count=scored,
                hard_gate_pass_fraction=compute_hard_gate_pass_fraction(
                    passed_count=passed,
                    summaries=summaries,
                ),
                counterfactual_pairs=pairs,
                case_stats=case_stats,
                experiment_stats=experiment_stats,
                failure_counts=summarize_failure_kinds(
                    [summary.error_code for summary in summaries]
                ),
            )

    async def run_pairwise_judge(
        self,
        *,
        baseline_trial_id: UUID,
        candidate_trial_id: UUID,
        case_id: str,
        comparison_group_id: str,
        judge_run_id: UUID,
        judge: PairwiseJudge,
        position_variant: PositionVariant = PositionVariant.BASELINE,
    ) -> tuple[EvalTrialPair, EvalPairwiseJudgeResult]:
        """Run one Pairwise Judge execution between two Trials.

        Loads the persisted ``EvalEvidenceItem`` rows for both Trials,
        converts them back to in-memory ``EvidenceItem`` rows and builds
        ``AuthorizedView``s filtered to ``JUDGE_ALLOWED_KINDS``. The
        ``Pair`` is idempotently persisted via ``get_or_create_pair``
        (UNIQUE pair_hash) so a re-run for the same baseline/candidate
        trial tuple with unchanged output bytes re-uses the existing Pair
        row — even across different ``comparison_group_id`` / Judge
        versions. ``comparison_group_id`` is recorded on the Result row,
        NOT on the Pair row.

        This method deliberately does NOT re-collect evidence: it consumes
        whatever the Trial's last ``attach_evidence``/``grade_trial`` pass
        already persisted. The caller is responsible for ensuring both
        Trials have been graded first.

        Returns the persisted (pair, result) tuple.

        Idempotency: ``eval_pairwise_judge_results.judge_run_id`` has a
        UNIQUE constraint and ``EvalPairwiseSweepItem.judge_run_id`` is
        deterministic (uuid5 over ``sweep|pair_hash|position|model|
        prompt|rubric``). Crash recovery replays the SAME ``judge_run_id``,
        which would otherwise hit ``IntegrityError`` on the result INSERT.
        Guard the replay by looking up an existing row first; if found,
        return it without invoking the Provider. The Pair row is already
        idempotent via ``get_or_create_pair``.
        """

        # Phase 1: freeze authorized inputs and idempotently materialize Pair.
        # This transaction must end before the potentially slow Provider call.
        async with session_transaction(self._session):
            # Idempotency guard: an existing Result for this deterministic
            # judge_run_id means a prior attempt already succeeded (typical
            # crash-recovery replay). Return it verbatim; do NOT call the
            # Provider again — that would either duplicate the result row
            # (IntegrityError) or burn cost on an already-attributed run.
            # We need a Pair row first to scope the lookup. Pair rows are
            # themselves idempotent (UNIQUE pair_hash on content). Build
            # the domain Pair purely to compute its hash; if the row
            # already exists we re-use it, otherwise we create it below.
            baseline_view_probe = await self.build_judge_view(baseline_trial_id)
            candidate_view_probe = await self.build_judge_view(candidate_trial_id)
            pair_domain_probe = build_pair(
                baseline_trial_id=baseline_trial_id,
                candidate_trial_id=candidate_trial_id,
                case_id=case_id,
                baseline_view=baseline_view_probe,
                candidate_view=candidate_view_probe,
            )
            existing_pair_row = await self._evals.get_pair_by_hash(
                pair_domain_probe.pair_hash()
            )
            if existing_pair_row is not None:
                existing_result = await self._evals.get_judge_result(
                    existing_pair_row.id, judge_run_id
                )
                if existing_result is not None:
                    return existing_pair_row, existing_result

            baseline_view = baseline_view_probe
            candidate_view = candidate_view_probe
            pair_domain = pair_domain_probe
            judge_input = build_judge_input(
                pair=pair_domain,
                judge_run_id=judge_run_id,
                baseline_view=baseline_view,
                candidate_view=candidate_view,
                position_variant=position_variant,
            )
            await self._evals.get_or_create_pair(
                EvalTrialPair(
                    baseline_trial_id=baseline_trial_id,
                    candidate_trial_id=candidate_trial_id,
                    case_id=case_id,
                    pair_hash=pair_domain.pair_hash(),
                    input_hash=judge_input.input_hash,
                    allowed_evidence_kinds=sorted(
                        kind.value for kind in JUDGE_ALLOWED_KINDS
                    ),
                    judge_prompt_version=judge_input.judge_prompt_version,
                    judge_rubric_version=judge_input.judge_rubric_version,
                )
            )

        # Phase 2: external I/O with no service-owned database transaction.
        result = await judge.judge(judge_input)

        # Phase 3: short idempotent result write. Re-check judge_run_id because
        # another retry may have completed while this Provider call was in flight.
        async with session_transaction(self._session):
            persisted_pair = await self._evals.get_pair_by_hash(pair_domain.pair_hash())
            if persisted_pair is None:
                raise RuntimeError("pair row disappeared before Judge result persist")
            existing_result = await self._evals.get_judge_result_by_run(judge_run_id)
            if existing_result is not None:
                return persisted_pair, existing_result
            result_row = await self._evals.get_or_create_judge_result(
                EvalPairwiseJudgeResult(
                    pair_id=persisted_pair.id,
                    judge_run_id=result.judge_run_id,
                    judge_run_status=result.judge_run_status,
                    position_variant=position_variant.value,
                    comparison_group_id=comparison_group_id,
                    raw_display_winner=result.raw_display_winner,
                    normalized_winner=result.normalized_winner,
                    raw_dimension_verdicts=result.raw_dimension_verdicts,
                    normalized_dimension_verdicts=result.normalized_dimension_verdicts,
                    confidence=result.confidence,
                    rationale=result.rationale,
                    model_id=result.model_id,
                    prompt_version=result.prompt_config.prompt_version,
                    rubric_version=result.prompt_config.rubric_version,
                    input_hash=result.input_hash,
                    raw_output_hash=result.usage.raw_output_hash if result.usage else None,
                    tokens_in=result.usage.tokens_in if result.usage else 0,
                    tokens_out=result.usage.tokens_out if result.usage else 0,
                    latency_ms=result.usage.latency_ms if result.usage else 0,
                    calibrated=False,
                )
            )
            return persisted_pair, result_row

    async def build_judge_view(self, trial_id: UUID):  # type: ignore[no-untyped-def]
        """Re-hydrate the authorized evidence view for one Trial.

        Loads the persisted ``EvalEvidenceItem`` rows and converts them to
        in-memory ``EvidenceItem`` rows (the ``AuthorizedView`` payload).
        The view is then filtered to ``JUDGE_ALLOWED_KINDS`` via
        ``authorize``. ``EvidenceKind`` is imported lazily so the module
        top stays clean of pairwise-only symbols.
        """

        from evals.v2.graders.base import EvidenceKind

        rows = await self._evals.list_evidence_items(trial_id)
        items = [
            EvidenceItem(
                id=row.id,
                trial_id=row.trial_id,
                kind=EvidenceKind(row.kind),
                source_type=row.source_type,
                source_id=row.source_id or "",
                content_hash=row.content_hash,
                projection=row.projection_json,
                sensitivity=row.sensitivity,
            )
            for row in rows
        ]
        return authorize(
            trial_id=trial_id, items=items, allowed_kinds=JUDGE_ALLOWED_KINDS
        )

    @staticmethod
    def _frozen_hash(experiment: EvalExperiment) -> str:
        return canonical_sha256(
            {
                "dataset_id": experiment.dataset_id,
                "dataset_version": experiment.dataset_version,
                "dataset_hash": experiment.dataset_hash,
                "git_commit": experiment.git_commit,
                "graph_version": experiment.graph_version,
                "feature_stage": experiment.feature_stage,
                "prompt_version": experiment.prompt_version,
                "model_version": experiment.model_version,
                "tool_version": experiment.tool_version,
                "context_version": experiment.context_version,
                "memory_version": experiment.memory_version,
                "search_version": experiment.search_version,
                "eval_harness_version": experiment.eval_harness_version,
                "execution_mode": experiment.execution_mode,
                "variant_role": experiment.variant_role,
                "agent_variant": experiment.agent_variant,
            }
        )


def _trial_seed(group_hash: str, trial_index: int) -> int:
    """Derive a deterministic int seed for one Trial.

    The ``group_hash`` argument is whichever deterministic fingerprint the
    caller chose: pre-PR-8 callers pass the Case fixture_hash, while PR-8
    counterfactual groups pass ``canonical_sha256(counterfactual_group_id)``
    so paired variants land on the same base trajectory. The function itself
    is hash-agnostic.
    """

    return int(group_hash[:8], 16) ^ trial_index


def _trial_summary_from_snapshot(trial: EvalTrial):  # type: ignore[no-untyped-def]
    """Build a ``TrialSummary`` from a persisted Trial + its outcome snapshot.

    Mirrors ``evals/v2/experiment_runner.py::_summarize`` but reads only DB
    state (no live ``RunOutcome``). The snapshot was written by
    ``TrialRunner._finalize_trial`` for completed / degraded trials; for
    cancelled / failed / timed-out trials we fall back to ORM fields plus
    the persisted ``error_code``.
    """

    from evals.v2.experiment_runner import TrialSummary

    snapshot = trial.outcome_snapshot_json
    run_status: str | None = None
    result_kind: str | None = None
    tokens_in = 0
    tokens_out = 0
    latency_ms = 0
    terminal_events_count = 0
    tool_call_count = 0
    if snapshot:
        run_block = snapshot.get("run")
        if isinstance(run_block, dict):
            run_status_raw = run_block.get("status")
            run_status = str(run_status_raw) if run_status_raw is not None else None
            result_kind_raw = run_block.get("result_kind")
            result_kind = (
                str(result_kind_raw) if result_kind_raw is not None else None
            )
            tokens_in = int(run_block.get("total_tokens_in", 0) or 0)
            tokens_out = int(run_block.get("total_tokens_out", 0) or 0)
            latency_ms = int(run_block.get("total_latency_ms", 0) or 0)
        events = snapshot.get("events")
        if isinstance(events, list):
            # Snapshot events are dicts; terminal ones carry event_type in
            # {"run.completed","run.degraded","run.failed","run.cancelled"}.
            terminal_types = {
                "run.completed",
                "run.degraded",
                "run.failed",
                "run.cancelled",
            }
            terminal_events_count = sum(
                1
                for ev in events
                if isinstance(ev, dict)
                and ev.get("event_type") in terminal_types
            )
        tool_calls = snapshot.get("tool_calls")
        if isinstance(tool_calls, list):
            tool_call_count = len(tool_calls)

    if trial.status == "completed" and run_status in {"completed", "degraded"}:
        report_status = "completed"
    elif run_status == "cancelled" or trial.status == "cancelled":
        report_status = "cancelled"
    elif trial.status in {"failed", "timed_out"}:
        report_status = "failed"
    else:
        report_status = trial.status

    return TrialSummary(
        trial_id=trial.id,
        case_id=trial.case_id,
        status=report_status,
        run_status=run_status,
        result_kind=result_kind,
        tokens_in=tokens_in or trial.tokens_in,
        tokens_out=tokens_out or trial.tokens_out,
        latency_ms=latency_ms or trial.latency_ms,
        error_code=trial.error_code,
        terminal_event_count=terminal_events_count,
        tool_call_count=tool_call_count,
        variant=trial.variant,
        counterfactual_group_id=trial.counterfactual_group_id,
        trial_index=trial.trial_index,
    )
