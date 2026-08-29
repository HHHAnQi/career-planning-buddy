"""Persistence operations for the Eval V2 control plane."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import (
    EvalEvidenceItem,
    EvalExperiment,
    EvalPairwiseCalibrationReport,
    EvalPairwiseHumanAnnotation,
    EvalPairwiseJudgeResult,
    EvalPairwiseSweep,
    EvalPairwiseSweepItem,
    EvalScore,
    EvalTrial,
    EvalTrialPair,
)


class EvalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_experiment(self, experiment: EvalExperiment) -> EvalExperiment:
        self._session.add(experiment)
        await self._session.flush()
        return experiment

    async def get_experiment(
        self, experiment_id: UUID, *, for_update: bool = False
    ) -> EvalExperiment | None:
        statement: Select[tuple[EvalExperiment]] = select(EvalExperiment).where(
            EvalExperiment.id == experiment_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_experiments(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EvalExperiment]:
        """Paginated listing for ``GET /api/v1/eval/runs``.

        Ordering is ``created_at desc`` so the newest Experiments surface
        first; pagination is the standard limit/offset style used by the
        agent-runs list endpoint.
        """

        statement = select(EvalExperiment)
        if status is not None:
            statement = statement.where(EvalExperiment.status == status)
        statement = (
            statement.order_by(EvalExperiment.created_at.desc())
            .limit(max(1, min(limit, 200)))
            .offset(max(0, offset))
        )
        result = await self._session.execute(statement)
        return list(result.scalars())

    async def create_trials(self, trials: Sequence[EvalTrial]) -> list[EvalTrial]:
        self._session.add_all(trials)
        await self._session.flush()
        return list(trials)

    async def list_trials(self, experiment_id: UUID) -> list[EvalTrial]:
        result = await self._session.execute(
            select(EvalTrial)
            .where(EvalTrial.experiment_id == experiment_id)
            .order_by(EvalTrial.case_id, EvalTrial.trial_index)
        )
        return list(result.scalars())

    async def get_trial(
        self, trial_id: UUID, *, for_update: bool = False
    ) -> EvalTrial | None:
        statement: Select[tuple[EvalTrial]] = select(EvalTrial).where(
            EvalTrial.id == trial_id
        )
        if for_update:
            # ``with_for_update`` writes ``FOR UPDATE``. Combine with
            # ``populate_existing`` so the row is re-loaded from the DB rather
            # than served from the session identity map -- otherwise a Trial
            # committed via a different session (TrialRunner's own factory)
            # stays invisible to the caller. PR-5 fix: clears the cached
            # ``status='pending'`` image that pre-dates the attach step.
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def attach_trial_outcome(
        self,
        trial_id: UUID,
        *,
        status: str,
        run_id: UUID | None,
        outcome_snapshot: dict[str, object] | None,
        transcript_hash: str | None,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        finished_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Freeze a Trial's terminal outcome.

        Uses an explicit UPDATE keyed on ``trial_id`` so the write does not
        depend on the ORM identity map of the originating session (the
        TrialRunner calls this from a different session than the one that
        created the Trial row).

        Satisfies ``ck_eval_trials_completed_outcome``: a ``completed`` Trial
        must carry a ``run_id`` + ``outcome_snapshot_json`` + 64-hex
        ``transcript_hash``. Non-completed terminal states (``failed``,
        ``timed_out``, ``cancelled``) may omit the snapshot/hash and record an
        ``error_code`` instead.
        """

        await self._session.execute(
            update(EvalTrial)
            .where(EvalTrial.id == trial_id)
            .values(
                status=status,
                run_id=run_id,
                outcome_snapshot_json=outcome_snapshot,
                transcript_hash=transcript_hash,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                error_code=error_code,
                error_message=error_message,
                finished_at=finished_at,
            )
        )

    async def mark_trial_running(
        self, trial_id: UUID, *, started_at: datetime
    ) -> None:
        """Transition a pending Trial to ``running`` before execution.

        Uses an explicit UPDATE keyed on ``trial_id`` so the write does not
        depend on the ORM identity map of the originating session (the
        TrialRunner calls this from a different session than the one that
        created the Trial row). Mirrors the DB trigger's legal
        ``pending -> running`` transition.
        """

        await self._session.execute(
            update(EvalTrial)
            .where(EvalTrial.id == trial_id, EvalTrial.status == "pending")
            .values(status="running", started_at=started_at)
        )

    async def count_nonterminal_trials(self, experiment_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count(EvalTrial.id)).where(
                EvalTrial.experiment_id == experiment_id,
                EvalTrial.status.in_(("pending", "running")),
            )
        )
        return int(result.scalar_one())

    async def cancel_nonterminal_trials(
        self,
        experiment_id: UUID,
        *,
        finished_at: datetime,
        error_code: str,
        error_message: str,
    ) -> None:
        """Cancel pending/running Trials without overwriting terminal evidence."""

        await self._session.execute(
            update(EvalTrial)
            .where(
                EvalTrial.experiment_id == experiment_id,
                EvalTrial.status.in_(("pending", "running")),
            )
            .values(
                status="cancelled",
                finished_at=finished_at,
                error_code=error_code,
                error_message=error_message,
            )
        )

    async def create_score(self, score: EvalScore) -> EvalScore:
        self._session.add(score)
        await self._session.flush()
        return score

    async def list_scores(self, trial_id: UUID) -> list[EvalScore]:
        result = await self._session.execute(
            select(EvalScore)
            .where(EvalScore.trial_id == trial_id)
            .order_by(EvalScore.domain, EvalScore.grader_name)
        )
        return list(result.scalars())

    async def delete_evidence_for_trial(self, trial_id: UUID) -> None:
        """Remove any previously-collected evidence so the next collect can
        re-insert rows whose ids differ if content changed.

        Used before ``create_evidence_items`` to make re-collection
        deterministic; the foreign key ON DELETE CASCADE would otherwise
        orphan rows that no longer match the projection.
        """

        from sqlalchemy import delete

        await self._session.execute(
            delete(EvalEvidenceItem).where(EvalEvidenceItem.trial_id == trial_id)
        )

    async def create_evidence_items(
        self, items: Sequence[EvalEvidenceItem]
    ) -> list[EvalEvidenceItem]:
        self._session.add_all(items)
        await self._session.flush()
        return list(items)

    async def list_evidence_items(self, trial_id: UUID) -> list[EvalEvidenceItem]:
        result = await self._session.execute(
            select(EvalEvidenceItem)
            .where(EvalEvidenceItem.trial_id == trial_id)
            .order_by(EvalEvidenceItem.kind, EvalEvidenceItem.source_type)
        )
        return list(result.scalars())

    # ---------------------------------------------------------- PR-9c.1
    # Pairwise Judge persistence. Two tables: ``eval_trial_pairs`` (stable
    # pair identity, UNIQUE on pair_hash) and ``eval_pairwise_judge_results``
    # (one row per physical Judge execution, carries comparison_group_id).
    # ``get_or_create_pair`` is idempotent: an IntegrityError on the UNIQUE
    # pair_hash means another session raced ahead, so we re-read. The
    # comparison_group_id lives on Result rows only.

    async def get_or_create_pair(self, pair: EvalTrialPair) -> EvalTrialPair:
        """Idempotently insert a Pair, returning the persisted row.

        A savepoint contains a concurrent UNIQUE collision so unrelated
        writes in the caller's outer transaction are never rolled back.
        """

        existing = await self.get_pair_by_hash(pair.pair_hash)
        if existing is not None:
            return existing
        try:
            async with self._session.begin_nested():
                self._session.add(pair)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_pair_by_hash(pair.pair_hash)
            if existing is None:
                raise
            return existing
        return pair

    async def get_pair(self, pair_id: UUID) -> EvalTrialPair | None:
        result = await self._session.execute(
            select(EvalTrialPair).where(EvalTrialPair.id == pair_id)
        )
        return result.scalar_one_or_none()

    async def get_pair_by_hash(self, pair_hash: str) -> EvalTrialPair | None:
        result = await self._session.execute(
            select(EvalTrialPair).where(EvalTrialPair.pair_hash == pair_hash)
        )
        return result.scalar_one_or_none()

    async def create_judge_result(
        self, result: EvalPairwiseJudgeResult
    ) -> EvalPairwiseJudgeResult:
        self._session.add(result)
        await self._session.flush()
        return result

    async def get_or_create_judge_result(
        self, result: EvalPairwiseJudgeResult
    ) -> EvalPairwiseJudgeResult:
        """Persist a deterministic Judge run once, including concurrent retries."""

        existing = await self.get_judge_result_by_run(result.judge_run_id)
        if existing is not None:
            return existing
        try:
            async with self._session.begin_nested():
                self._session.add(result)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_judge_result_by_run(result.judge_run_id)
            if existing is None:
                raise
            return existing
        return result

    async def get_judge_result(
        self, pair_id: UUID, judge_run_id: UUID
    ) -> EvalPairwiseJudgeResult | None:
        result = await self._session.execute(
            select(EvalPairwiseJudgeResult).where(
                EvalPairwiseJudgeResult.pair_id == pair_id,
                EvalPairwiseJudgeResult.judge_run_id == judge_run_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_judge_result_by_run(
        self, judge_run_id: UUID
    ) -> EvalPairwiseJudgeResult | None:
        """Look up a JudgeResult by its deterministic ``judge_run_id``
        alone (no pair scope). Used by the Executor's recovery path,
        where we have the SweepItem's ``judge_run_id`` but not the
        Pair id (the SweepItem's ``pair_id`` is the FK to the Pair
        row, but the Result row's ``pair_id`` may match either the
        SweepItem's Pair or its sibling's — both Items of a Pair share
        the same Pair row, so their Results carry that same pair_id;
        nonetheless the recovery path prefers to resolve Result-Pair
        coupling via the globally-unique ``judge_run_id``).
        """

        result = await self._session.execute(
            select(EvalPairwiseJudgeResult).where(
                EvalPairwiseJudgeResult.judge_run_id == judge_run_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_judge_result_by_id(
        self, result_id: UUID
    ) -> EvalPairwiseJudgeResult | None:
        """Look up a JudgeResult by its primary key — primarily a test
        convenience so a caller that has an opaque ``judge_result_id``
        FK can fetch the row's ``judge_run_id`` for further joins."""

        result = await self._session.execute(
            select(EvalPairwiseJudgeResult).where(
                EvalPairwiseJudgeResult.id == result_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_judge_results_by_pair(
        self, pair_id: UUID
    ) -> list[EvalPairwiseJudgeResult]:
        result = await self._session.execute(
            select(EvalPairwiseJudgeResult)
            .where(EvalPairwiseJudgeResult.pair_id == pair_id)
            .order_by(EvalPairwiseJudgeResult.created_at)
        )
        return list(result.scalars())

    async def list_judge_results_by_comparison_group(
        self, comparison_group_id: str
    ) -> list[tuple[EvalTrialPair, EvalPairwiseJudgeResult]]:
        """Join through ``eval_trial_pairs`` to surface every Judge row in a
        comparison group. ``comparison_group_id`` is recorded on the Result
        row (not the Pair row), so the filter is on
        ``EvalPairwiseJudgeResult.comparison_group_id``. Returns (pair,
        result) tuples so the caller can group by pair without a second
        lookup."""

        result = await self._session.execute(
            select(EvalTrialPair, EvalPairwiseJudgeResult)
            .join(
                EvalPairwiseJudgeResult,
                EvalPairwiseJudgeResult.pair_id == EvalTrialPair.id,
            )
            .where(
                EvalPairwiseJudgeResult.comparison_group_id == comparison_group_id
            )
            .order_by(EvalTrialPair.case_id, EvalPairwiseJudgeResult.created_at)
        )
        return [
            (pair, judge_result)
            for pair, judge_result in result.all()
        ]


    # ====================================================================
    # PR-9c.2 Calibration workflow repository
    # ====================================================================
    #
    # Grouped by domain (per Plan v2 execution order):
    #   1) Sweep lifecycle — create / get / list / mark terminal / cancel
    #   2) SweepItem materialize / claim / recovery work list
    #   3) Annotation queries / inserts (idempotent, no ON CONFLICT)
    #   4) Pair locking for primary/adjudication serial decisions
    #   5) Calibration report snapshot
    #
    # Business invariants (third-primary rejection, adjudicator-third-person
    # check, vector disagreement, input_hash integrity) live in the Service
    # layer. The Repository issues only DB actions. The Service issues
    # SELECT FOR UPDATE on the Pair row via ``lock_pair_for_update``.
    # --------------------------------------------------------------------

    # ----- 1) Sweep lifecycle ------------------------------------------

    async def create_sweep(self, sweep: EvalPairwiseSweep) -> EvalPairwiseSweep:
        self._session.add(sweep)
        await self._session.flush()
        return sweep

    async def get_sweep(self, sweep_id: UUID) -> EvalPairwiseSweep | None:
        result = await self._session.execute(
            select(EvalPairwiseSweep).where(EvalPairwiseSweep.id == sweep_id)
        )
        return result.scalar_one_or_none()

    async def get_sweep_by_comparison_group(
        self, comparison_group_id: str
    ) -> EvalPairwiseSweep | None:
        result = await self._session.execute(
            select(EvalPairwiseSweep).where(
                EvalPairwiseSweep.comparison_group_id == comparison_group_id
            )
        )
        return result.scalar_one_or_none()

    async def list_sweeps_by_dataset(
        self, dataset_id: str, dataset_version: str
    ) -> list[EvalPairwiseSweep]:
        result = await self._session.execute(
            select(EvalPairwiseSweep)
            .where(
                EvalPairwiseSweep.dataset_id == dataset_id,
                EvalPairwiseSweep.dataset_version == dataset_version,
            )
            .order_by(EvalPairwiseSweep.started_at.desc())
        )
        return list(result.scalars())

    async def mark_sweep_running(self, sweep_id: UUID) -> None:
        """Transition ``queued`` → ``running``. Idempotent if already running."""

        await self._session.execute(
            update(EvalPairwiseSweep)
            .where(
                EvalPairwiseSweep.id == sweep_id,
                EvalPairwiseSweep.status == "queued",
            )
            .values(status="running")
        )

    async def mark_sweep_terminal(
        self, sweep_id: UUID, *, status: str
    ) -> None:
        """Set terminal status + ``terminal_at = now()``.

        Caller (Service) is responsible for choosing ``completed`` vs
        ``failed``. The cancelled terminal state is reserved for the
        Executor (Commit 3).
        """

        if status not in ("completed", "failed"):
            raise ValueError(f"invalid terminal status: {status!r}")
        await self._session.execute(
            update(EvalPairwiseSweep)
            .where(EvalPairwiseSweep.id == sweep_id)
            .values(status=status, terminal_at=func.now())
        )

    async def increment_sweep_completed_run(
        self, sweep_id: UUID, *, completed_pair: bool, position_pair: bool
    ) -> None:
        """Atomically bump ``completed_judge_run_count`` and (conditionally)
        ``completed_pair_count`` / ``position_pair_count``.

        Uses Postgres ``UPDATE ... SET col = col + 1`` semantics so
        concurrent Item completions serialize correctly. The Service is
        responsible for computing ``completed_pair`` and ``position_pair``
        flags by inspecting the sibling item before invoking this.
        """

        # Read existing counters under row lock to compute conditional
        # increments cleanly. We use SELECT ... FOR UPDATE so two
        # concurrent calls serialize against the same row.
        result = await self._session.execute(
            select(EvalPairwiseSweep)
            .where(EvalPairwiseSweep.id == sweep_id)
            .with_for_update()
        )
        sweep = result.scalar_one_or_none()
        if sweep is None:
            raise ValueError(f"sweep {sweep_id} not found")
        sweep.completed_judge_run_count += 1
        if completed_pair:
            sweep.completed_pair_count += 1
        if position_pair:
            sweep.position_pair_count += 1

    async def increment_sweep_failed_run(self, sweep_id: UUID) -> None:
        """Atomically bump ``failed_judge_run_count``."""

        result = await self._session.execute(
            select(EvalPairwiseSweep)
            .where(EvalPairwiseSweep.id == sweep_id)
            .with_for_update()
        )
        sweep = result.scalar_one_or_none()
        if sweep is None:
            raise ValueError(f"sweep {sweep_id} not found")
        sweep.failed_judge_run_count += 1

    async def set_sweep_cancel_requested_at(
        self, sweep_id: UUID, at: datetime
    ) -> EvalPairwiseSweep | None:
        """Stage a cancel request by stamping ``cancel_requested_at``.

        Per supplementary constraint #8, this method does NOT touch
        ``status``. The Executor (Commit 3) cooperatively reads this
        field, drains in-flight work, then sets ``status='cancelled'`` +
        ``terminal_at``. Returns the updated row, or ``None`` if the
        Sweep was not found OR was already in a terminal state (caller
        treats that as no-op).
        """

        result = await self._session.execute(
            select(EvalPairwiseSweep)
            .where(EvalPairwiseSweep.id == sweep_id)
            .with_for_update()
        )
        sweep = result.scalar_one_or_none()
        if sweep is None:
            return None
        if sweep.terminal_at is not None:
            # Already terminal — idempotent no-op (cancel_requested stays
            # whatever the previous caller set it to).
            return sweep
        if sweep.cancel_requested_at is None:
            sweep.cancel_requested_at = at
        return sweep

    # ----- 2) SweepItem materialize / claim / recover ------------------

    async def create_sweep_items(
        self, items: list[EvalPairwiseSweepItem]
    ) -> list[EvalPairwiseSweepItem]:
        """Bulk-insert frozen SweepItem rows.

        Items MUST arrive pre-keyed with their deterministic
        ``judge_run_id`` (Service.compute_deterministic_judge_run_id).
        ``UNIQUE(sweep_id, pair_id, position_variant)`` +
        ``UNIQUE(judge_run_id)`` ensure a crashed Sweep's retry reuses
        the SAME rows instead of allocating new ids.
        """

        if not items:
            return []
        self._session.add_all(items)
        await self._session.flush()
        return items

    async def get_sweep_item(
        self, sweep_id: UUID, pair_id: UUID, position_variant: str
    ) -> EvalPairwiseSweepItem | None:
        result = await self._session.execute(
            select(EvalPairwiseSweepItem).where(
                EvalPairwiseSweepItem.sweep_id == sweep_id,
                EvalPairwiseSweepItem.pair_id == pair_id,
                EvalPairwiseSweepItem.position_variant == position_variant,
            )
        )
        return result.scalar_one_or_none()

    async def list_sweep_items(self, sweep_id: UUID) -> list[EvalPairwiseSweepItem]:
        result = await self._session.execute(
            select(EvalPairwiseSweepItem)
            .where(EvalPairwiseSweepItem.sweep_id == sweep_id)
            .order_by(
                EvalPairwiseSweepItem.pair_hash,
                EvalPairwiseSweepItem.position_variant,
            )
        )
        return list(result.scalars())

    async def list_recoverable_sweep_items(
        self, sweep_id: UUID
    ) -> list[EvalPairwiseSweepItem]:
        """The recovery work list: items that are queued or running.

        Per supplementary constraint #3, recovery NEVER re-runs the
        Sampler. It only consumes already-frozen SweepItem rows. Items
        that reached ``completed`` / ``failed`` / ``cancelled`` are
        skipped — they are already attributable to their judge_run_id.
        """

        result = await self._session.execute(
            select(EvalPairwiseSweepItem)
            .where(
                EvalPairwiseSweepItem.sweep_id == sweep_id,
                EvalPairwiseSweepItem.status.in_(("queued", "running")),
            )
            .order_by(
                EvalPairwiseSweepItem.pair_hash,
                EvalPairwiseSweepItem.position_variant,
            )
        )
        return list(result.scalars())

    async def list_running_sweep_items(
        self, sweep_id: UUID
    ) -> list[EvalPairwiseSweepItem]:
        """Items currently in ``running`` state — the subset that
        ``_recover_running_items`` MUST reconcile on process restart.

        Distinct from ``list_recoverable_sweep_items`` which also
        includes ``queued`` (and is what the executor's main pump
        consumes). The recovery path specifically needs the orphaned
        ``running`` Items because ``_claim_one_item`` only picks up
        ``queued`` — without reconciliation these would stall the Sweep.
        """

        result = await self._session.execute(
            select(EvalPairwiseSweepItem)
            .where(
                EvalPairwiseSweepItem.sweep_id == sweep_id,
                EvalPairwiseSweepItem.status == "running",
            )
            .order_by(
                EvalPairwiseSweepItem.pair_hash,
                EvalPairwiseSweepItem.position_variant,
            )
        )
        return list(result.scalars())

    async def mark_sweep_item_completed(
        self,
        item_id: UUID,
        *,
        judge_result_id: UUID,
    ) -> bool:
        """Flip a SweepItem to terminal ``completed``.

        Returns ``True`` iff the row transitioned from
        ``('queued','running') → 'completed'`` this call. Returns
        ``False`` if the Item was already terminal (the caller MUST NOT
        bump Sweep counters when ``False`` is returned — otherwise
        double counting).

        Per supplementary constraint #3: ``completed`` Items MUST carry
        a ``judge_result_id`` — a Judge Run that produced
        ``invalid_structured_output`` still has a row in
        ``eval_pairwise_judge_results`` and is associated here. The caller
        may not pass ``judge_result_id=None``; this is enforced by the
        type signature (no Optional).
        """

        result = await self._session.execute(
            update(EvalPairwiseSweepItem)
            .where(
                EvalPairwiseSweepItem.id == item_id,
                EvalPairwiseSweepItem.status.in_(("queued", "running")),
            )
            .values(
                status="completed",
                judge_result_id=judge_result_id,
                terminal_at=func.now(),
            )
            .returning(EvalPairwiseSweepItem.id)
        )
        return result.scalar_one_or_none() is not None

    async def mark_sweep_item_failed(
        self,
        item_id: UUID,
        *,
        error_code: str,
    ) -> bool:
        """Flip a SweepItem to terminal ``failed``.

        Returns ``True`` iff a transition happened; ``False`` if the Item
        was already terminal (callers MUST NOT bump counters on
        ``False``). ``failed`` is reserved for control-plane failures —
        a Judge Run that returned ``invalid_structured_output`` still
        has an ``eval_pairwise_judge_results`` row, so the Item is
        ``completed`` with that result, NOT ``failed``.
        """

        result = await self._session.execute(
            update(EvalPairwiseSweepItem)
            .where(
                EvalPairwiseSweepItem.id == item_id,
                EvalPairwiseSweepItem.status.in_(("queued", "running")),
            )
            .values(
                status="failed",
                error_code=error_code,
                terminal_at=func.now(),
            )
            .returning(EvalPairwiseSweepItem.id)
        )
        return result.scalar_one_or_none() is not None

    # ----- 3) Annotation queries / inserts ----------------------------

    async def lock_pair_for_update(
        self, pair_id: UUID
    ) -> EvalTrialPair | None:
        """``SELECT pair row FOR UPDATE`` — primary call path for
        serial primary/adjudication decisions in the Service layer.

        Acquiring a row-level lock on the Pair serializes any concurrent
        annotation submissions against the same Pair within the same
        transaction; the Service then reads existing primaries, validates
        the contract (max-2 primaries, third-person adjudicator, vector
        disagreement present), and INSERTs under the lock. Two
        overlapping submissions therefore serialize — the loser sees the
        winner's INSERT and fails its precondition.
        """

        result = await self._session.execute(
            select(EvalTrialPair)
            .where(EvalTrialPair.id == pair_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_annotations_by_pair(
        self, pair_id: UUID
    ) -> list[EvalPairwiseHumanAnnotation]:
        """All annotations (primary + adjudication) for one Pair."""

        result = await self._session.execute(
            select(EvalPairwiseHumanAnnotation)
            .where(EvalPairwiseHumanAnnotation.pair_id == pair_id)
            .order_by(EvalPairwiseHumanAnnotation.created_at)
        )
        return list(result.scalars())

    async def list_annotations_by_sweep(
        self, sweep_id: UUID
    ) -> list[EvalPairwiseHumanAnnotation]:
        result = await self._session.execute(
            select(EvalPairwiseHumanAnnotation)
            .where(EvalPairwiseHumanAnnotation.sweep_id == sweep_id)
            .order_by(
                EvalPairwiseHumanAnnotation.pair_id,
                EvalPairwiseHumanAnnotation.created_at,
            )
        )
        return list(result.scalars())

    async def find_annotation(
        self,
        *,
        dataset_id: str,
        pair_id: UUID,
        reviewer_id: str,
        review_input_hash: str,
        is_adjudication: bool | None = None,
    ) -> EvalPairwiseHumanAnnotation | None:
        """Exact (dataset, pair, reviewer, review-surface) lookup used
        by the idempotent annotation submit path.

        ``is_adjudication`` narrows the lookup when the caller wants to
        find only a primary (``False``) or only an adjudication
        (``True``) row. This matters when the SAME reviewer could
        legitimately hold BOTH a primary and an adjudication on the same
        (pair, surface) pair — but in our contract that is forbidden (an
        adjudicator MUST be a different reviewer from the primaries),
        so the role filter is a defensive carve-out for tests rather
        than a production branch."""

        stmt = select(EvalPairwiseHumanAnnotation).where(
            EvalPairwiseHumanAnnotation.dataset_id == dataset_id,
            EvalPairwiseHumanAnnotation.pair_id == pair_id,
            EvalPairwiseHumanAnnotation.reviewer_id == reviewer_id,
            EvalPairwiseHumanAnnotation.review_input_hash == review_input_hash,
        )
        if is_adjudication is not None:
            stmt = stmt.where(
                EvalPairwiseHumanAnnotation.is_adjudication == is_adjudication
            )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_annotation(
        self, annotation: EvalPairwiseHumanAnnotation
    ) -> EvalPairwiseHumanAnnotation:
        """Insert a new annotation. Idempotency / conflict resolution is
        the Service's responsibility via ``find_annotation`` first."""

        self._session.add(annotation)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # UNIQUE(...) violation surfaced to Service as a 409 conflict
            # or a hard FK failure. Re-raise so the caller can decide.
            raise exc
        return annotation

    # ----- 4) Calibration report snapshot ------------------------------

    async def find_calibration_report_by_input_hash(
        self, input_hash: str
    ) -> EvalPairwiseCalibrationReport | None:
        result = await self._session.execute(
            select(EvalPairwiseCalibrationReport).where(
                EvalPairwiseCalibrationReport.input_hash == input_hash
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_calibration_report(
        self, dataset_id: str, dataset_version: str
    ) -> EvalPairwiseCalibrationReport | None:
        """Most recently created report for a given dataset identity."""

        result = await self._session.execute(
            select(EvalPairwiseCalibrationReport)
            .where(
                EvalPairwiseCalibrationReport.dataset_id == dataset_id,
                EvalPairwiseCalibrationReport.dataset_version == dataset_version,
            )
            .order_by(EvalPairwiseCalibrationReport.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_calibration_reports(
        self, dataset_id: str, dataset_version: str
    ) -> list[EvalPairwiseCalibrationReport]:
        """History (newest first)."""

        result = await self._session.execute(
            select(EvalPairwiseCalibrationReport)
            .where(
                EvalPairwiseCalibrationReport.dataset_id == dataset_id,
                EvalPairwiseCalibrationReport.dataset_version == dataset_version,
            )
            .order_by(EvalPairwiseCalibrationReport.created_at.desc())
        )
        return list(result.scalars())

    async def create_calibration_report(
        self, report: EvalPairwiseCalibrationReport
    ) -> EvalPairwiseCalibrationReport:
        """Insert a new report row. Caller (Service) computes input_hash
        + content_hash and verifies no content_hash mismatch on an
        existing input_hash hit beforehand."""

        self._session.add(report)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # UNIQUE(input_hash) hit on a race we missed — surface the
            # integrity violation to the caller; the Service should
            # re-read and decide whether content_hash matches (idempotent
            # return) or diverges (integrity error).
            raise exc
        return report
