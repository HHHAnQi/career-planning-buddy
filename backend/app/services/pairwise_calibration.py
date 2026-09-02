"""PR-9c.2 Pairwise Calibration Service.

Owns the calibration workflow's transactional business logic:

* :func:`deterministic_judge_run_id`            — uuid5 from
  (sweep_id, pair_hash, position_variant, judge identity). Stability
  under recovery is what makes ``EvalPairwiseSweepItem`` idempotent.
* :func:`materialize_sweep_items`               — convert a frozen export
  bundle into ``EvalPairwiseSweepItem`` rows (the work list) with their
  deterministic judge_run_ids already populated. Sweep itself is flipped
  ``queued → running`` afterwards.
* :meth:`PairwiseCalibrationService.submit_annotation` — the idempotent
  primary-annotation path under ``SELECT pair FOR UPDATE``. Same payload →
  return existing (200). Same surface, different payload → 409 conflict.
  Third primary on a Pair already at 2 primaries → 409 primary-full.
* :meth:`PairwiseCalibrationService.submit_adjudication` — also under
  pair lock; adjudicator must be a third distinct reviewer; vector
  disagreement (overall OR any of 5 dimensions) must already exist
  between the two primaries.
* :meth:`PairwiseCalibrationService.request_sweep_cancel` — stages
  ``cancel_requested_at`` only; does NOT touch ``status`` (Executor in
  Commit 3 owns the cooperative stop + ``terminal_at`` bookkeeping).
* :meth:`PairwiseCalibrationService.create_or_reuse_calibration_report`
  — computes ``input_hash`` over judge and annotation snapshots, then
  ``content_hash`` over the report payload. Same input + same content →
  idempotent return; same input + different content → integrity error.

Service never uses ``ON CONFLICT DO NOTHING``. Repository never makes
business decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.models.eval import (
    EvalPairwiseCalibrationReport,
    EvalPairwiseHumanAnnotation,
    EvalPairwiseSweep,
    EvalPairwiseSweepItem,
    EvalTrialPair,
)
from app.repositories.evals import EvalRepository
from evals.v2.contracts import canonical_sha256
from evals.v2.pairwise import PositionVariant, build_pair
from evals.v2.pairwise_review_surface import (
    FrozenReviewSurface,
    build_frozen_review_surface,
)

# uuid5 namespace for deterministic judge_run_id derivation. Fixed value
# (not the public DNS namespace) so a future change to the seed format is
# obvious in this constant.
PAIRWISE_JUDGE_RUN_NAMESPACE = UUID("c4b5e6f7-0000-4000-8000-000000009c20")

_DIMENSIONS = ("actionability", "alignment", "personalization", "clarity", "consistency")
_REPORT_SCHEMA_VERSION = "pairwise-calibration-report/v1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PairwiseCalibrationError(AppError):
    """Base for all 4xx Pairwise Calibration errors."""


def _conflict(code: str, message: str) -> PairwiseCalibrationError:
    return PairwiseCalibrationError(
        code=code, message=message, status_code=409
    )


def _not_found(code: str, message: str) -> PairwiseCalibrationError:
    return PairwiseCalibrationError(
        code=code, message=message, status_code=404
    )


def _integrity(code: str, message: str) -> PairwiseCalibrationError:
    return PairwiseCalibrationError(
        code=code, message=message, status_code=500
    )


# ---------------------------------------------------------------------------
# Deterministic judge_run_id
# ---------------------------------------------------------------------------


def deterministic_judge_run_id(
    *,
    sweep_id: UUID,
    pair_hash: str,
    position_variant: PositionVariant | str,
    judge_model_id: str,
    judge_prompt_version: str,
    judge_rubric_version: str,
) -> UUID:
    """Deterministic UUID for one (sweep, pair, position, judge-identity).

    Invariants:

    * Same inputs ⇒ same UUID (so a crashed Sweep replay yields the SAME
      row in ``eval_pairwise_judge_results`` AND the SAME
      ``EvalPairwiseSweepItem`` row — never a duplicate).
    * The seed string is a pipe-delimited tuple; field changes (new judge
      model, new rubric) flip the UUID, intentionally producing a NEW
      SweepItem row so the re-evaluation is attributable to the new
      identity.
    """

    position_value = (
        position_variant.value
        if isinstance(position_variant, PositionVariant)
        else position_variant
    )
    seed = "|".join(
        [
            str(sweep_id),
            pair_hash,
            position_value,
            judge_model_id,
            judge_prompt_version,
            judge_rubric_version,
        ]
    )
    return uuid5(PAIRWISE_JUDGE_RUN_NAMESPACE, seed)


# ---------------------------------------------------------------------------
# Annotation payload views
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnnotationSubmission:
    """Reviewer-invariant inputs for an annotation.

    All fields are computed by the Service from the JWT subject +
    frozen review surface; the HTTP layer's submit body MUST NOT carry
    ``reviewer_id`` / ``position_variant`` / normalized verdicts /
    baseline-candidate mapping.

    ``raw_winner`` and ``raw_dimension_verdicts`` are display-position
    vocabulary (``a`` / ``b``) — exactly what the reviewer picked on
    their blinded surface.
    """

    pair_id: UUID
    sweep_id: UUID
    reviewer_id: str
    raw_winner: str
    raw_dimension_verdicts: dict[str, str]
    normalized_winner: str
    normalized_dimension_verdicts: dict[str, str]
    rationale: str | None
    is_adjudication: bool


@dataclass(frozen=True)
class SubmissionResult:
    """Outcome of an annotation/adjudication submit attempt.

    * ``status='existing'``: idempotent replay of an already-stored row.
    * ``status='created'``: a brand new annotation row was inserted.
    * ``annotation_id`` and ``annotation`` are populated in both cases so
      the HTTP layer can return the same shape (200 vs 201 differ only
      in status code).
    """

    status: str
    annotation: EvalPairwiseHumanAnnotation


@dataclass(frozen=True)
class CalibrationReportSnapshot:
    """Versioned calibration report content (before INSERT)."""

    dataset_id: str
    dataset_version: str
    source_sha256: str
    judge_model_id: str
    judge_prompt_version: str
    judge_rubric_version: str
    annotation_schema_version: str
    calibration_policy_version: str
    input_hash: str
    content_hash: str
    report_payload: dict[str, object]


# ---------------------------------------------------------------------------
# Sweep materialize
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepItemSeed:
    """One frozen Pair + Position the Sweep must Judge. Built from the
    Export JSONL by the Service 'materialize' caller; never reconstructed
    from in-memory experiments during recovery (constraint #3)."""

    pair_id: UUID
    pair_hash: str
    case_id: str
    baseline_trial_id: UUID
    candidate_trial_id: UUID
    baseline_output_hash: str
    candidate_output_hash: str
    frozen_review_surface_sha256: str
    display_a_trial_id: UUID
    display_b_trial_id: UUID
    position_variant: PositionVariant


@dataclass(frozen=True, slots=True)
class ReviewSurfaceContext:
    sweep: EvalPairwiseSweep
    pair: EvalTrialPair
    surface: FrozenReviewSurface


class PairwiseCalibrationService:
    """Pairwise calibration workflow service.

    Each public method opens its own ``session_transaction`` and exits
    atomically. The Service has no state beyond the session + repo.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = EvalRepository(session)

    async def build_review_surface(
        self,
        *,
        sweep_id: UUID,
        pair_id: UUID,
        reviewer_id: str,
    ) -> ReviewSurfaceContext:
        """Build one blinded surface from authorized, persisted Trial evidence."""

        pair_row = await self._repo.get_pair(pair_id)
        if pair_row is None:
            raise _not_found("EVAL_PAIR_NOT_FOUND", "pair not found")
        sweep = await self._repo.get_sweep(sweep_id)
        if sweep is None:
            raise _not_found("EVAL_SWEEP_NOT_FOUND", "sweep not found")

        from app.services.evals import EvalService

        eval_service = EvalService(self._session)
        baseline_view = await eval_service.build_judge_view(
            pair_row.baseline_trial_id
        )
        candidate_view = await eval_service.build_judge_view(
            pair_row.candidate_trial_id
        )
        pair = build_pair(
            baseline_trial_id=pair_row.baseline_trial_id,
            candidate_trial_id=pair_row.candidate_trial_id,
            case_id=pair_row.case_id,
            baseline_view=baseline_view,
            candidate_view=candidate_view,
        )
        if pair.pair_hash() != pair_row.pair_hash:
            raise _integrity(
                "EVAL_REVIEW_EVIDENCE_HASH_MISMATCH",
                "Persisted Pair identity no longer matches its authorized evidence",
            )
        surface = build_frozen_review_surface(
            pair=pair,
            reviewer_id=reviewer_id,
            rubric=[],
            rubric_version=sweep.judge_rubric_version,
            annotation_schema_version=sweep.annotation_schema_version,
        )
        item = await self._repo.get_sweep_item(
            sweep_id,
            pair_id,
            surface.position_variant.value,
        )
        if item is None:
            raise _not_found(
                "EVAL_PAIR_NOT_IN_SWEEP",
                "pair is not part of the requested sweep",
            )
        if (
            item.pair_hash != pair_row.pair_hash
            or item.frozen_review_surface_sha256
            != surface.frozen_review_surface_sha256
            or item.display_a_trial_id != UUID(surface.display_a_trial_id)
            or item.display_b_trial_id != UUID(surface.display_b_trial_id)
        ):
            raise _integrity(
                "EVAL_REVIEW_SURFACE_INTEGRITY_FAILED",
                "SweepItem does not match the reconstructed review surface",
            )
        return ReviewSurfaceContext(sweep=sweep, pair=pair_row, surface=surface)

    # ---------------------------- materialize ---------------------------

    async def materialize_sweep_items(
        self,
        *,
        sweep: EvalPairwiseSweep,
        seeds: list[SweepItemSeed],
        annotation_schema_version: str,
    ) -> list[EvalPairwiseSweepItem]:
        """Freeze the Sweep's work list and flip ``queued → running``.

        Caller (Commit 3 SweepExecutor) supplies a ``seeds`` list derived
        from the frozen Export JSONL by combining each Pair with BOTH
        position variants. We compute each item's deterministic
        ``judge_run_id`` here so the rows can be replayed on recovery
        without re-running the sampler.

        ``UNIQUE(sweep_id, pair_id, position_variant)`` +
        ``UNIQUE(judge_run_id)`` enforce exactly-once materialization even
        if the caller mistakenly re-enters this method.
        """

        if not seeds:
            raise PairwiseCalibrationError(
                code="EVAL_SWEEP_EMPTY",
                message="cannot materialize a Sweep with zero items",
                status_code=422,
            )
        async with session_transaction(self._session):
            items: list[EvalPairwiseSweepItem] = []
            seen_keys: set[tuple[UUID, str]] = set()
            for seed in seeds:
                judge_run_id = deterministic_judge_run_id(
                    sweep_id=sweep.id,
                    pair_hash=seed.pair_hash,
                    position_variant=seed.position_variant,
                    judge_model_id=sweep.judge_model_id,
                    judge_prompt_version=sweep.judge_prompt_version,
                    judge_rubric_version=sweep.judge_rubric_version,
                )
                key = (seed.pair_id, seed.position_variant.value)
                if key in seen_keys:
                    raise PairwiseCalibrationError(
                        code="EVAL_SWEEP_SEEDS_DUPLICATED",
                        message=(
                            f"duplicate seed for pair={seed.pair_id} "
                            f"position={seed.position_variant.value}"
                        ),
                        status_code=422,
                    )
                seen_keys.add(key)
                items.append(
                    EvalPairwiseSweepItem(
                        sweep_id=sweep.id,
                        pair_id=seed.pair_id,
                        position_variant=seed.position_variant.value,
                        case_id=seed.case_id,
                        pair_hash=seed.pair_hash,
                        baseline_trial_id=seed.baseline_trial_id,
                        candidate_trial_id=seed.candidate_trial_id,
                        baseline_output_hash=seed.baseline_output_hash,
                        candidate_output_hash=seed.candidate_output_hash,
                        display_a_trial_id=seed.display_a_trial_id,
                        display_b_trial_id=seed.display_b_trial_id,
                        frozen_review_surface_sha256=seed.frozen_review_surface_sha256,
                        judge_run_id=judge_run_id,
                        status="queued",
                    )
                )

            # Idempotent insertion: if the row already exists (recovery
            # path that called materialize again), we surface a clean
            # CHECK/UNIQUE error rather than silently double-allocating.
            inserted = await self._repo.create_sweep_items(items)

            # Refresh the sweep's counts FROM the items we just wrote so
            # the contract ``requested_pair_count * 2 ==
            # requested_judge_run_count`` aligns.
            expected_pairs = len(seeds) // 2
            expected_runs = len(seeds)
            if (
                sweep.requested_pair_count != expected_pairs
                or sweep.requested_judge_run_count != expected_runs
            ):
                raise _integrity(
                    "EVAL_SWEEP_COUNTS_MISMATCH",
                    "sweep.requested_*_count do not match seed count",
                )

            await self._repo.mark_sweep_running(sweep.id)
            return inserted

    # ----------------------- annotation submit --------------------------

    async def submit_annotation(
        self,
        submission: AnnotationSubmission,
        *,
        dataset_id: str,
        dataset_version: str,
        annotation_schema_version: str,
        rubric_version: str,
        judge_prompt_version: str,
        judge_model_id: str,
        frozen_review_surface_sha256: str,
        position_variant: PositionVariant,
        display_a_trial_id: UUID,
        display_b_trial_id: UUID,
    ) -> SubmissionResult:
        """Idempotent primary-annotation submit.

        Sequence inside one transaction:

        1. ``SELECT pair FOR UPDATE`` — serializes concurrent submitters.
        2. ``find_annotation(dataset, pair, reviewer, review_input_hash)``
           — exact-key lookup.
           * hit + same ``submission_hash`` → return existing (200).
           * hit + different ``submission_hash`` → 409 payload conflict.
           * miss → continue.
        3. Count primary annotations EXCLUDING this reviewer. If two
           OTHER primaries already exist → 409 primary-full. The reviewer
           themselves is allowed to re-submit (handled in step 2).
        4. INSERT and return ``created``.
        """

        review_input_hash = frozen_review_surface_sha256
        submission_hash = self._compute_submission_hash(submission)

        async with session_transaction(self._session):
            pair_row = await self._repo.lock_pair_for_update(submission.pair_id)
            if pair_row is None:
                raise _not_found(
                    "EVAL_PAIR_NOT_FOUND",
                    f"pair {submission.pair_id} not found",
                )

            existing = await self._repo.find_annotation(
                dataset_id=dataset_id,
                pair_id=submission.pair_id,
                reviewer_id=submission.reviewer_id,
                review_input_hash=review_input_hash,
            )
            if existing is not None:
                if existing.submission_hash == submission_hash:
                    return SubmissionResult(status="existing", annotation=existing)
                raise _conflict(
                    "EVAL_ANNOTATION_PAYLOAD_CONFLICT",
                    (
                        "annotation exists for this reviewer + review surface "
                        "with a different payload"
                    ),
                )

            annotations = await self._repo.list_annotations_by_pair(
                submission.pair_id
            )
            other_primaries = [
                a
                for a in annotations
                if not a.is_adjudication and a.reviewer_id != submission.reviewer_id
            ]
            if len(other_primaries) >= 2:
                raise _conflict(
                    "EVAL_ANNOTATION_PRIMARY_REVIEWER_FULL",
                    "this Pair already has two primary reviewers",
                )

            new_annotation = self._build_annotation_row(
                submission=submission,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                annotation_schema_version=annotation_schema_version,
                rubric_version=rubric_version,
                judge_prompt_version=judge_prompt_version,
                judge_model_id=judge_model_id,
                frozen_review_surface_sha256=frozen_review_surface_sha256,
                position_variant=position_variant,
                display_a_trial_id=display_a_trial_id,
                display_b_trial_id=display_b_trial_id,
                submission_hash=submission_hash,
            )
            inserted = await self._repo.create_annotation(new_annotation)
            return SubmissionResult(status="created", annotation=inserted)

    # ----------------------- adjudication submit ------------------------

    async def submit_adjudication(
        self,
        submission: AnnotationSubmission,
        *,
        dataset_id: str,
        dataset_version: str,
        annotation_schema_version: str,
        rubric_version: str,
        judge_prompt_version: str,
        judge_model_id: str,
        frozen_review_surface_sha256: str,
        position_variant: PositionVariant,
        display_a_trial_id: UUID,
        display_b_trial_id: UUID,
    ) -> SubmissionResult:
        """Adjudication submit. Pre-conditions checked under pair lock:

        * Exactly two primary annotations exist on this Pair.
        * Neither primary reviewer matches this adjudicator's id.
        * The two primaries disagree on overall OR any of 5 dimensions.
        * No adjudication row already exists for this
          ``(pair_id, review_input_hash)``.
        """

        if not submission.is_adjudication:
            raise PairwiseCalibrationError(
                code="EVAL_ADJUDICATION_ROLE_REQUIRED",
                message="adjudication submit requires is_adjudication=True",
                status_code=422,
            )
        submission_hash = self._compute_submission_hash(submission)

        async with session_transaction(self._session):
            pair_row = await self._repo.lock_pair_for_update(submission.pair_id)
            if pair_row is None:
                raise _not_found(
                    "EVAL_PAIR_NOT_FOUND",
                    f"pair {submission.pair_id} not found",
                )

            existing = await self._repo.find_annotation(
                dataset_id=dataset_id,
                pair_id=submission.pair_id,
                reviewer_id=submission.reviewer_id,
                review_input_hash=frozen_review_surface_sha256,
                is_adjudication=True,
            )
            if existing is not None:
                if existing.submission_hash == submission_hash:
                    return SubmissionResult(status="existing", annotation=existing)
                raise _conflict(
                    "EVAL_ANNOTATION_PAYLOAD_CONFLICT",
                    (
                        "adjudication exists for this reviewer + review surface "
                        "with a different payload"
                    ),
                )

            annotations = await self._repo.list_annotations_by_pair(
                submission.pair_id
            )
            primaries = [a for a in annotations if not a.is_adjudication]
            other_adjudications = [a for a in annotations if a.is_adjudication]

            if len(primaries) != 2:
                raise _conflict(
                    "EVAL_ADJUDICATION_PRECONDITION_FAILED",
                    "adjudication requires exactly two primary annotations",
                )
            if any(p.reviewer_id == submission.reviewer_id for p in primaries):
                raise _conflict(
                    "EVAL_ADJUDICATION_PRECONDITION_FAILED",
                    "adjudicator must be a different reviewer from the primaries",
                )
            if other_adjudications:
                raise _conflict(
                    "EVAL_ADJUDICATION_PRECONDITION_FAILED",
                    "an adjudication row already exists for this Pair",
                )
            p1, p2 = primaries
            if not _primary_pair_disagrees(p1, p2):
                raise _conflict(
                    "EVAL_ADJUDICATION_PRECONDITION_FAILED",
                    "primaries agree on overall and every dimension — "
                    "no adjudication required",
                )

            new_annotation = self._build_annotation_row(
                submission=submission,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                annotation_schema_version=annotation_schema_version,
                rubric_version=rubric_version,
                judge_prompt_version=judge_prompt_version,
                judge_model_id=judge_model_id,
                frozen_review_surface_sha256=frozen_review_surface_sha256,
                position_variant=position_variant,
                display_a_trial_id=display_a_trial_id,
                display_b_trial_id=display_b_trial_id,
                submission_hash=submission_hash,
            )
            inserted = await self._repo.create_annotation(new_annotation)
            return SubmissionResult(status="created", annotation=inserted)

    # ---------------------------- cancel --------------------------------

    async def request_sweep_cancel(self, sweep_id: UUID) -> bool:
        """Stage ``cancel_requested_at`` on a non-terminal Sweep.

        Returns True if the field was newly stamped; False if the Sweep
        was missing, already terminal, or already had a cancel request.
        The Executor (Commit 3) reads ``cancel_requested_at`` and
        transitions the Sweep to ``cancelled`` once in-flight items drain.
        """

        async with session_transaction(self._session):
            sweep = await self._repo.get_sweep(sweep_id)
            if sweep is None:
                raise _not_found(
                    "EVAL_SWEEP_NOT_FOUND",
                    f"sweep {sweep_id} not found",
                )
            previous = sweep.cancel_requested_at
            updated = await self._repo.set_sweep_cancel_requested_at(
                sweep_id, datetime.now(UTC)
            )
            # Newly set means previous was None AND the row was non-terminal.
            return (
                updated is not None
                and previous is None
                and updated.cancel_requested_at is not None
            )

    # --------------------- calibration report ---------------------------

    async def create_or_reuse_calibration_report(
        self,
        *,
        dataset_id: str,
        dataset_version: str,
        source_sha256: str,
        judge_model_id: str,
        judge_prompt_version: str,
        judge_rubric_version: str,
        annotation_schema_version: str,
        calibration_policy_version: str,
        sweep_ids: list[UUID],
        judge_result_snapshot: list[dict[str, object]],
        annotation_snapshot: list[dict[str, object]],
        report_payload: dict[str, object],
        requested_by: str,
    ) -> tuple[str, EvalPairwiseCalibrationReport]:
        """Compute input/content hashes and insert-or-reuse a report.

        ``input_hash`` covers dataset/version/source_sha + judge identity
        + all sweep ids + every JudgeResult and annotation snapshot we
        are about to compute on. ``content_hash`` covers the report
        payload itself.

        Decision matrix:

        * input_hash UNKNOWN → INSERT new; status ``created``.
        * input_hash KNOWN + same content_hash → return existing;
          status ``existing``.
        * input_hash KNOWN + different content_hash → 500 integrity error
          (a non-deterministic report is a bug, not a recoverable state).
        """

        snapshot_block = {
            "schema_version": _REPORT_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "source_sha256": source_sha256,
            "judge_model_id": judge_model_id,
            "judge_prompt_version": judge_prompt_version,
            "judge_rubric_version": judge_rubric_version,
            "annotation_schema_version": annotation_schema_version,
            "calibration_policy_version": calibration_policy_version,
            "sweep_ids_sorted": sorted(str(s) for s in sweep_ids),
            "judge_results_sorted": sorted(
                judge_result_snapshot,
                key=lambda r: str(r.get("judge_run_id") or r.get("judge_result_id") or ""),
            ),
            "annotations_sorted": sorted(
                annotation_snapshot,
                key=lambda a: str(a.get("annotation_id") or a.get("submission_hash") or ""),
            ),
        }
        input_hash = canonical_sha256(snapshot_block)
        content_hash = canonical_sha256(report_payload)

        async with session_transaction(self._session):
            existing = await self._repo.find_calibration_report_by_input_hash(
                input_hash
            )
            if existing is not None:
                if existing.content_hash == content_hash:
                    return "existing", existing
                raise _integrity(
                    "EVAL_CALIBRATION_INTEGRITY_VIOLATION",
                    (
                        "calibration report input_hash already exists with a "
                        "different content_hash — non-deterministic report"
                    ),
                )
            report = EvalPairwiseCalibrationReport(
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                source_sha256=source_sha256,
                judge_model_id=judge_model_id,
                judge_prompt_version=judge_prompt_version,
                judge_rubric_version=judge_rubric_version,
                annotation_schema_version=annotation_schema_version,
                calibration_policy_version=calibration_policy_version,
                input_hash=input_hash,
                content_hash=content_hash,
                report_payload=report_payload,
                requested_by=requested_by,
            )
            inserted = await self._repo.create_calibration_report(report)
            return "created", inserted

    # ---------------------------- helpers -------------------------------

    @staticmethod
    def _compute_submission_hash(submission: AnnotationSubmission) -> str:
        """Hash over the reviewer-controlled payload + role.

        ``reviewer_id`` and ``pair_id`` are intentionally included so two
        reviewers submitting the same verdict vector on the same pair do
        NOT collide (they are different review events).
        """

        return canonical_sha256({
            "schema_version": "pairwise-annotation-submission/v1",
            "pair_id": str(submission.pair_id),
            "sweep_id": str(submission.sweep_id),
            "reviewer_id": submission.reviewer_id,
            "is_adjudication": submission.is_adjudication,
            "raw_winner": submission.raw_winner,
            "raw_dimension_verdicts": dict(submission.raw_dimension_verdicts),
            "normalized_winner": submission.normalized_winner,
            "normalized_dimension_verdicts": dict(
                submission.normalized_dimension_verdicts
            ),
            "rationale": submission.rationale,
        })

    @staticmethod
    def _build_annotation_row(
        *,
        submission: AnnotationSubmission,
        dataset_id: str,
        dataset_version: str,
        annotation_schema_version: str,
        rubric_version: str,
        judge_prompt_version: str,
        judge_model_id: str,
        frozen_review_surface_sha256: str,
        position_variant: PositionVariant,
        display_a_trial_id: UUID,
        display_b_trial_id: UUID,
        submission_hash: str,
    ) -> EvalPairwiseHumanAnnotation:
        raw = submission.raw_dimension_verdicts
        norm = submission.normalized_dimension_verdicts
        return EvalPairwiseHumanAnnotation(
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            sweep_id=submission.sweep_id,
            pair_id=submission.pair_id,
            reviewer_id=submission.reviewer_id,
            reviewer_role="adjudicator" if submission.is_adjudication else "primary",
            is_adjudication=submission.is_adjudication,
            annotation_schema_version=annotation_schema_version,
            rubric_version=rubric_version,
            judge_prompt_version=judge_prompt_version,
            judge_model_id=judge_model_id,
            frozen_review_surface_sha256=frozen_review_surface_sha256,
            position_variant=position_variant.value,
            display_a_trial_id=display_a_trial_id,
            display_b_trial_id=display_b_trial_id,
            raw_winner=submission.raw_winner,
            raw_dim_actionability=raw["actionability"],
            raw_dim_alignment=raw["alignment"],
            raw_dim_personalization=raw["personalization"],
            raw_dim_clarity=raw["clarity"],
            raw_dim_consistency=raw["consistency"],
            normalized_winner=submission.normalized_winner,
            norm_dim_actionability=norm["actionability"],
            norm_dim_alignment=norm["alignment"],
            norm_dim_personalization=norm["personalization"],
            norm_dim_clarity=norm["clarity"],
            norm_dim_consistency=norm["consistency"],
            review_input_hash=frozen_review_surface_sha256,
            submission_hash=submission_hash,
            rationale=submission.rationale,
        )


def _primary_pair_disagrees(
    p1: EvalPairwiseHumanAnnotation, p2: EvalPairwiseHumanAnnotation
) -> bool:
    """Any difference (overall or per-dimension) between two primaries.

    Per supplementary constraint: any single dimension divergence counts
    as disagreement, not just overall winner mismatch.
    """

    if p1.normalized_winner != p2.normalized_winner:
        return True
    for dim in _DIMENSIONS:
        if getattr(p1, f"norm_dim_{dim}") != getattr(p2, f"norm_dim_{dim}"):
            return True
    return False
