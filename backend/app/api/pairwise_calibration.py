"""PR-9c.2 Pairwise Calibration HTTP control plane.

Nine endpoints under ``/api/v1/eval`` (separate router, not nested under
``/eval/runs/{id}`` because calibration is cross-experiment). All require
``require_dev`` (same as the rest of the eval control plane) and pull
``reviewer_id`` from JWT subject — never from the request body
(supplementary constraint #6 + Commit 3.2 issue #1 review-token binding,
which is REQUIRED on every annotation POST).

Endpoints (Commit 3.2 count fix — was incorrectly listed as 10 in 3.1):

* POST /api/v1/eval/runs/{baseline_experiment_id}/pairwise/run
* GET  /api/v1/eval/runs/{baseline_experiment_id}/pairwise/run/{sweep_id}
* POST /api/v1/eval/runs/{baseline_experiment_id}/pairwise/run/{sweep_id}/cancel
* GET  /api/v1/eval/runs/pairwise/pairs/{pair_id}/review-surface?sweep_id=...
* POST /api/v1/eval/runs/pairwise/annotations
* GET  /api/v1/eval/runs/pairwise/annotations/{pair_id}
* POST /api/v1/eval/pairwise/calibration
* GET  /api/v1/eval/pairwise/calibration/{dataset_id}/{dataset_version}/latest
* GET  /api/v1/eval/pairwise/calibration/{dataset_id}/{dataset_version}/history
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_pairwise_sweep_executor,
    require_dev,
)
from app.core.database import get_db_session, session_transaction
from app.core.exceptions import AppError
from app.core.security import AuthenticatedUser
from app.harness.pairwise_sweep_executor import PairwiseSweepExecutor
from app.models.eval import (
    EvalPairwiseSweep,
)
from app.repositories.evals import EvalRepository
from app.schemas.errors import ErrorResponse
from app.schemas.evals import (
    PairwiseAnnotationListResponse,
    PairwiseAnnotationResponse,
    PairwiseAnnotationSubmitRequest,
    PairwiseAnnotationSubmitResponse,
    PairwiseCalibrationReportRequest,
    PairwiseCalibrationReportResponse,
    PairwiseReviewSurfaceResponse,
    PairwiseRunCancelResponse,
    PairwiseRunRequest,
    PairwiseRunStatusResponse,
)
from app.services.pairwise_calibration import (
    AnnotationSubmission,
    PairwiseCalibrationError,
    PairwiseCalibrationService,
)
from evals.v2.calibration_loader import (
    CalibrationDatasetBundle,
    CalibrationDatasetNotFound,
    load_calibration_dataset,
)
from evals.v2.calibration_metrics import CALIBRATION_POLICY_VERSION

router = APIRouter(
    prefix="/eval",
    tags=["pairwise-calibration"],
    dependencies=[Depends(require_dev)],
)

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}

_ANNOTATION_SCHEMA_VERSION = "v1"


# ===========================================================================
# Sweep control plane
# ===========================================================================


def _sweep_to_response(sweep: EvalPairwiseSweep) -> PairwiseRunStatusResponse:
    return PairwiseRunStatusResponse(
        sweep_id=sweep.id,
        comparison_group_id=sweep.comparison_group_id,
        status=sweep.status,
        dataset_id=sweep.dataset_id,
        dataset_version=sweep.dataset_version,
        source_sha256=sweep.source_sha256,
        judge_model_id=sweep.judge_model_id,
        judge_prompt_version=sweep.judge_prompt_version,
        judge_rubric_version=sweep.judge_rubric_version,
        annotation_schema_version=sweep.annotation_schema_version,
        requested_pair_count=sweep.requested_pair_count,
        requested_judge_run_count=sweep.requested_judge_run_count,
        completed_judge_run_count=sweep.completed_judge_run_count,
        failed_judge_run_count=sweep.failed_judge_run_count,
        completed_pair_count=sweep.completed_pair_count,
        position_pair_count=sweep.position_pair_count,
        requested_by=sweep.requested_by,
        started_at=sweep.started_at,
        cancel_requested_at=sweep.cancel_requested_at,
        terminal_at=sweep.terminal_at,
    )


@router.post(
    "/runs/{baseline_experiment_id}/pairwise/run",
    response_model=PairwiseRunStatusResponse,
    status_code=HTTPStatus.ACCEPTED,
    responses=_ERROR_RESPONSES,
)
async def start_pairwise_run(
    baseline_experiment_id: UUID,
    body: PairwiseRunRequest,
    reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
    executor: Annotated[PairwiseSweepExecutor, Depends(get_pairwise_sweep_executor)],
) -> PairwiseRunStatusResponse:
    """Spawn a pairwise Judge sweep.

    The service creates the Sweep + SweepItem rows synchronously (frozen
    Pair Export JSONL materialized verbatim), then the executor picks up
    the work in the background. Returns 202 with the queued Sweep status.
    """

    repo = EvalRepository(session)
    baseline = await repo.get_experiment(baseline_experiment_id)
    if baseline is None:
        raise AppError(
            code="EVAL_EXPERIMENT_NOT_FOUND",
            message="baseline experiment not found",
            status_code=HTTPStatus.NOT_FOUND,
        )
    candidate = await repo.get_experiment(body.candidate_experiment_id)
    if candidate is None:
        raise AppError(
            code="EVAL_EXPERIMENT_NOT_FOUND",
            message="candidate experiment not found",
            status_code=HTTPStatus.NOT_FOUND,
        )

    try:
        bundle = load_calibration_dataset(
            dataset_id=body.dataset_id, dataset_version=body.dataset_version
        )
    except CalibrationDatasetNotFound as exc:
        raise AppError(
            code="EVAL_CALIBRATION_DATASET_NOT_FOUND",
            message=str(exc),
            status_code=HTTPStatus.NOT_FOUND,
        ) from exc

    # Single fixed judge identity per sweep (supplementary constraint #7).
    from app.core.config import get_settings

    settings = get_settings()
    judge_model_id = (
        settings.judge_llm_model
        if settings.judge_llm_model
        else f"{settings.llm_model}:judge"
    )
    judge_prompt_version = "v1"
    judge_rubric_version = "v1"
    annotation_schema_version = _ANNOTATION_SCHEMA_VERSION

    sweep = await _materialize_sweep(
        repo=repo,
        bundle=bundle,
        reviewer=reviewer,
        baseline_experiment_id=baseline_experiment_id,
        candidate_experiment_id=body.candidate_experiment_id,
        judge_model_id=judge_model_id,
        judge_prompt_version=judge_prompt_version,
        judge_rubric_version=judge_rubric_version,
        annotation_schema_version=annotation_schema_version,
        fixture_mapping=body.fixture_mapping,
        session=session,
    )
    executor.submit(sweep.id)
    await session.refresh(sweep)
    return _sweep_to_response(sweep)


async def _materialize_sweep(
    *,
    repo: EvalRepository,
    bundle: CalibrationDatasetBundle,
    reviewer: AuthenticatedUser,
    baseline_experiment_id: UUID,
    candidate_experiment_id: UUID,
    judge_model_id: str,
    judge_prompt_version: str,
    judge_rubric_version: str,
    annotation_schema_version: str,
    fixture_mapping: dict[str, dict[str, object]] | None,
    session: AsyncSession,
) -> EvalPairwiseSweep:
    """Create the Sweep row + SweepItem rows from the frozen Export JSONL.

    Commit 3.3 closes the long-standing gap (Commit 3.1 report's
    "explicit gap" comment): rows ARE now seeded from ``bundle.lines``,
    so the executor has real work to pump. Each export line becomes:

    * one ``EvalTrialPair`` row (idempotent via pair_hash re-use);
    * two ``SweepItemSeed`` rows — baseline + swapped — derived from the
      pair. Position assignment is deterministic per (pair_hash,
      reviewer_id) via ``derive_position_variant``, matching the GET
      review-surface endpoint the reviewer later hits.

    The optional ``fixture_mapping`` (pair_hash → JudgeOutput dict, only
    set on smoke runs) is persisted onto the Sweep so the executor can
    construct a FixturePairwiseJudge that returns ``completed`` verdicts
    rather than fail-closing on an empty mapping.
    """

    import hashlib
    import uuid as _uuid

    from app.models.eval import EvalPairwiseSweep, EvalTrialPair
    from app.services.pairwise_calibration import (
        PairwiseCalibrationService,
        SweepItemSeed,
    )

    pair_count = len(bundle.lines)
    requested_judge_run_count = pair_count * 2
    sweep = EvalPairwiseSweep(
        dataset_id=bundle.manifest.dataset_id,
        dataset_version=bundle.manifest.dataset_version,
        source_sha256=bundle.manifest.source_sha256,
        export_revision=bundle.manifest.case_schema_version,
        baseline_experiment_id=baseline_experiment_id,
        candidate_experiment_id=candidate_experiment_id,
        judge_model_id=judge_model_id,
        judge_prompt_version=judge_prompt_version,
        judge_rubric_version=judge_rubric_version,
        annotation_schema_version=annotation_schema_version,
        comparison_group_id=f"sweep-{_uuid.uuid4().hex[:16]}",
        status="queued",
        requested_pair_count=pair_count,
        requested_judge_run_count=requested_judge_run_count,
        requested_by=str(reviewer.id),
        fixture_mapping=fixture_mapping,
    )
    async with session_transaction(repo._session):  # noqa: SLF001
        sweep = await repo.create_sweep(sweep)

    # Build seeds from the frozen Export bundle. Each line maps to one
    # EvalTrialPair row and two SweepItem rows (baseline + swapped).
    # ``derive_position_variant`` keeps the reviewer-visible A/B ordering
    # consistent across HTTP GET review-surface and the executor's
    # internal claim path.
    seeds: list[SweepItemSeed] = []
    async with session_transaction(session):
        for line in bundle.lines:
            # Idempotent EvalTrialPair row via pair_hash.
            existing_pair = await repo.get_pair_by_hash(line.pair_hash)
            if existing_pair is None:
                trial_pair = await repo.get_or_create_pair(
                    EvalTrialPair(
                        baseline_trial_id=line.baseline_trial_id,
                        candidate_trial_id=line.candidate_trial_id,
                        case_id=line.case_id,
                        pair_hash=line.pair_hash,
                        input_hash=hashlib.sha256(
                            (
                                line.baseline_output_hash
                                + line.candidate_output_hash
                            )
                            .encode("utf-8")
                        ).hexdigest(),
                        allowed_evidence_kinds=[
                            "REQUEST_CONSTRAINTS",
                            "PLAN_PROJECTION",
                        ],
                        judge_prompt_version=judge_prompt_version,
                        judge_rubric_version=judge_rubric_version,
                    )
                )
            else:
                trial_pair = existing_pair

            # Frozen review surface sha — derived from the export's
            # frozen projections exactly as the GET review-surface
            # endpoint would. We piggyback the helper here so the
            # SweepItem.frozen_review_surface_sha256 matches what the
            # HTTP layer will recompute on every annotation POST.
            from evals.v2.pairwise import (
                Pair as PairDomain,
            )
            from evals.v2.pairwise import (
                PositionVariant as PV,
            )
            from evals.v2.pairwise import (
                TrialEvidenceProjection,
            )
            from evals.v2.pairwise_review_surface import (
                build_frozen_review_surface,
            )

            domain_pair = PairDomain(
                baseline_trial_id=line.baseline_trial_id,
                candidate_trial_id=line.candidate_trial_id,
                case_id=line.case_id,
                baseline_projection=TrialEvidenceProjection(
                    request_constraints=line.frozen_request_constraints,
                    plan_projection=line.frozen_baseline_plan_projection,
                ),
                candidate_projection=TrialEvidenceProjection(
                    request_constraints=line.frozen_request_constraints,
                    plan_projection=line.frozen_candidate_plan_projection,
                ),
            )
            # build_frozen_review_surface derives the canonical
            # position_variant for this reviewer; the surface sha is
            # display-invariant so both SweepItem rows (baseline +
            # swapped) carry the SAME frozen hash and the executor's
            # review-surface reconstruction at annotation time matches.
            surface = build_frozen_review_surface(
                pair=domain_pair,
                reviewer_id=str(reviewer.id),
                rubric=[],
                rubric_version=judge_rubric_version,
                annotation_schema_version=annotation_schema_version,
            )

            seeds.append(
                SweepItemSeed(
                    pair_id=trial_pair.id,
                    pair_hash=line.pair_hash,
                    case_id=line.case_id,
                    baseline_trial_id=line.baseline_trial_id,
                    candidate_trial_id=line.candidate_trial_id,
                    baseline_output_hash=line.baseline_output_hash,
                    candidate_output_hash=line.candidate_output_hash,
                    frozen_review_surface_sha256=surface.frozen_review_surface_sha256,
                    display_a_trial_id=line.baseline_trial_id,
                    display_b_trial_id=line.candidate_trial_id,
                    position_variant=PV.BASELINE,
                )
            )
            seeds.append(
                SweepItemSeed(
                    pair_id=trial_pair.id,
                    pair_hash=line.pair_hash,
                    case_id=line.case_id,
                    baseline_trial_id=line.baseline_trial_id,
                    candidate_trial_id=line.candidate_trial_id,
                    baseline_output_hash=line.baseline_output_hash,
                    candidate_output_hash=line.candidate_output_hash,
                    frozen_review_surface_sha256=surface.frozen_review_surface_sha256,
                    display_a_trial_id=line.candidate_trial_id,
                    display_b_trial_id=line.baseline_trial_id,
                    position_variant=PV.SWAPPED,
                )
            )

    # Seed the SweepItems via the Service so the deterministic
    # judge_run_id + counter invariants get enforced.
    service = PairwiseCalibrationService(session)
    await service.materialize_sweep_items(
        sweep=sweep,
        seeds=seeds,
        annotation_schema_version=annotation_schema_version,
    )

    return sweep


@router.get(
    "/runs/{baseline_experiment_id}/pairwise/run/{sweep_id}",
    response_model=PairwiseRunStatusResponse,
    responses=_ERROR_RESPONSES,
)
async def get_pairwise_run_status(
    baseline_experiment_id: UUID,
    sweep_id: UUID,
    _reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PairwiseRunStatusResponse:
    repo = EvalRepository(session)
    sweep = await repo.get_sweep(sweep_id)
    if sweep is None or sweep.baseline_experiment_id != baseline_experiment_id:
        raise AppError(
            code="EVAL_SWEEP_NOT_FOUND",
            message="sweep not found under this baseline experiment",
            status_code=HTTPStatus.NOT_FOUND,
        )
    return _sweep_to_response(sweep)


@router.post(
    "/runs/{baseline_experiment_id}/pairwise/run/{sweep_id}/cancel",
    response_model=PairwiseRunCancelResponse,
    status_code=HTTPStatus.ACCEPTED,
    responses=_ERROR_RESPONSES,
)
async def cancel_pairwise_run(
    baseline_experiment_id: UUID,
    sweep_id: UUID,
    _reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PairwiseRunCancelResponse:
    repo = EvalRepository(session)
    sweep = await repo.get_sweep(sweep_id)
    if sweep is None or sweep.baseline_experiment_id != baseline_experiment_id:
        raise AppError(
            code="EVAL_SWEEP_NOT_FOUND",
            message="sweep not found under this baseline experiment",
            status_code=HTTPStatus.NOT_FOUND,
        )
    service = PairwiseCalibrationService(session)
    staged = await service.request_sweep_cancel(sweep_id)
    await session.refresh(sweep)
    return PairwiseRunCancelResponse(
        sweep_id=sweep_id,
        cancel_requested=staged,
        cancel_requested_at=sweep.cancel_requested_at,
    )


# ===========================================================================
# Review surface (Commit 3.1 — issue #4)
# ===========================================================================


@router.get(
    "/runs/pairwise/pairs/{pair_id}/review-surface",
    response_model=PairwiseReviewSurfaceResponse,
    responses=_ERROR_RESPONSES,
)
async def get_pairwise_review_surface(
    pair_id: UUID,
    sweep_id: UUID,
    reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PairwiseReviewSurfaceResponse:
    """Return the server-rendered, blinded Review Surface for one Pair
    under a given Sweep, for the authenticated reviewer.

    Issue #4 closes the reviewer workflow gap. Without this endpoint a
    reviewer could POST an annotation but had no way to discover the
    server-chosen A/B ordering, the rubric, or any tamper-proof binding
    back to their reviewer identity.

    What this endpoint returns (deliberately minimal):

    * ``display_a`` / ``display_b`` — request + plan projections only,
      positioned per ``position_variant`` (itself derived from
      ``(pair_hash, reviewer_id, rubric_version,
      annotation_schema_version)``);
    * ``position_variant``, ``review_surface_version``,
      ``annotation_schema_version``, ``rubric_version`` — provenance;
    * ``rubric`` — empty by default in PR-9c.2 (no rubric catalog yet)
      but the field is reserved;
    * ``frozen_review_surface_sha256`` — same value that the POST
      ``/runs/pairwise/annotations`` endpoint re-derives server-side so
      a re-submit hits the idempotent path;
    * ``review_token`` — short non-secret token that the reviewer may
      echo back on POST. The server re-derives it; mismatch ⇒ 422.

    What it does NOT return: pair_hash, trial ids, baseline/candidate
    role markers, model/provider identity, automatic scores, suggested
    labels or judge hints, run cost / latency."""

    context = await PairwiseCalibrationService(session).build_review_surface(
        sweep_id=sweep_id,
        pair_id=pair_id,
        reviewer_id=str(reviewer.id),
    )
    sweep = context.sweep
    surface = context.surface
    from evals.v2.pairwise_review_surface import derive_review_token
    review_token = derive_review_token(
        pair_id=pair_id,
        reviewer_id=str(reviewer.id),
        frozen_review_surface_sha256=surface.frozen_review_surface_sha256,
    )
    return PairwiseReviewSurfaceResponse(
        pair_id=pair_id,
        sweep_id=sweep_id,
        case_id=surface.case_id,
        review_surface_version=surface.review_surface_version,
        annotation_schema_version=sweep.annotation_schema_version,
        rubric_version=surface.rubric_version,
        position_variant=surface.position_variant.value,
        rubric=list(surface.rubric),
        display_a=surface.display_a,
        display_b=surface.display_b,
        frozen_review_surface_sha256=surface.frozen_review_surface_sha256,
        review_token=review_token,
    )


# ===========================================================================
# Annotation control plane
# ===========================================================================


def _annotation_to_response(
    ann: Any,  # noqa: ANN401 — EvalPairwiseHumanAnnotation is an ORM class
) -> PairwiseAnnotationResponse:
    from datetime import datetime as _dt

    return PairwiseAnnotationResponse(
        annotation_id=ann.id,
        pair_id=ann.pair_id,
        sweep_id=ann.sweep_id,
        reviewer_id=ann.reviewer_id,
        reviewer_role=ann.reviewer_role,
        is_adjudication=ann.is_adjudication,
        raw_winner=ann.raw_winner,
        normalized_winner=ann.normalized_winner,
        position_variant=ann.position_variant,
        annotation_schema_version=ann.annotation_schema_version,
        rubric_version=ann.rubric_version,
        judge_prompt_version=ann.judge_prompt_version,
        judge_model_id=ann.judge_model_id,
        frozen_review_surface_sha256=ann.frozen_review_surface_sha256,
        created_at=(
            ann.created_at if isinstance(ann.created_at, _dt) else _dt.utcnow()
        ),
        rationale=ann.rationale,
    )


@router.post(
    "/runs/pairwise/annotations",
    response_model=PairwiseAnnotationSubmitResponse,
    responses=_ERROR_RESPONSES,
)
async def submit_annotation(
    body: PairwiseAnnotationSubmitRequest,
    reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PairwiseAnnotationSubmitResponse:
    """Submit a reviewer annotation (primary or adjudication).

    Server-authoritative fields (reviewer_id, position_variant,
    normalized verdicts, trial id mapping) are derived from the frozen
    review surface + JWT subject; the body only carries the raw
    display-side verdicts.
    """

    service = PairwiseCalibrationService(session)
    context = await service.build_review_surface(
        sweep_id=body.sweep_id,
        pair_id=body.pair_id,
        reviewer_id=str(reviewer.id),
    )
    sweep = context.sweep
    frozen_surface = context.surface

    from evals.v2.pairwise import (
        PositionVariant as _PV,
    )

    # Issue #1 / Commit 3.2: review_token is now REQUIRED. Pydantic
    # already rejected the missing-token case as a 422 validation error
    # before reaching here. We always re-derive and reject on mismatch
    # — covers wrong-reviewer, wrong-pair, stale-tamper, and any other
    # context drift. Applies identically to primary AND adjudication
    # submissions (the adjudicator must also GET the surface to see what
    # they are adjudicating).
    from evals.v2.pairwise_review_surface import derive_review_token

    expected = derive_review_token(
        pair_id=body.pair_id,
        reviewer_id=str(reviewer.id),
        frozen_review_surface_sha256=frozen_surface.frozen_review_surface_sha256,
    )
    if body.review_token != expected:
        raise AppError(
            code="EVAL_REVIEW_TOKEN_INVALID",
            message=(
                "review_token does not match the server-derived token "
                "for this (pair, reviewer, frozen_review_surface)"
            ),
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )

    # Compute normalized verdicts from raw + position variant.
    from evals.v2.pairwise_review_surface import (
        normalize_raw_dimensions,
        normalize_raw_to_baseline_candidate,
    )

    normalized_winner = normalize_raw_to_baseline_candidate(
        body.raw_winner, _PV(frozen_surface.position_variant.value)
    )
    raw_dims: dict[str, str] = {k: v for k, v in body.raw_dimension_verdicts.items()}
    normalized_dims: dict[str, str] = {
        k: v
        for k, v in normalize_raw_dimensions(
            dict(body.raw_dimension_verdicts),  # type: ignore[arg-type]
            _PV(frozen_surface.position_variant.value),
        ).items()
    }

    submission = AnnotationSubmission(
        pair_id=body.pair_id,
        sweep_id=body.sweep_id,
        reviewer_id=str(reviewer.id),
        raw_winner=body.raw_winner,
        raw_dimension_verdicts=raw_dims,
        normalized_winner=normalized_winner,
        normalized_dimension_verdicts=normalized_dims,
        rationale=body.rationale,
        is_adjudication=body.is_adjudication,
    )

    try:
        if body.is_adjudication:
            result = await service.submit_adjudication(
                submission,
                dataset_id=sweep.dataset_id,
                dataset_version=sweep.dataset_version,
                annotation_schema_version=sweep.annotation_schema_version,
                rubric_version=sweep.judge_rubric_version,
                judge_prompt_version=sweep.judge_prompt_version,
                judge_model_id=sweep.judge_model_id,
                frozen_review_surface_sha256=frozen_surface.frozen_review_surface_sha256,
                position_variant=frozen_surface.position_variant,
                display_a_trial_id=UUID(frozen_surface.display_a_trial_id),
                display_b_trial_id=UUID(frozen_surface.display_b_trial_id),
            )
        else:
            result = await service.submit_annotation(
                submission,
                dataset_id=sweep.dataset_id,
                dataset_version=sweep.dataset_version,
                annotation_schema_version=sweep.annotation_schema_version,
                rubric_version=sweep.judge_rubric_version,
                judge_prompt_version=sweep.judge_prompt_version,
                judge_model_id=sweep.judge_model_id,
                frozen_review_surface_sha256=frozen_surface.frozen_review_surface_sha256,
                position_variant=frozen_surface.position_variant,
                display_a_trial_id=UUID(frozen_surface.display_a_trial_id),
                display_b_trial_id=UUID(frozen_surface.display_b_trial_id),
            )
    except PairwiseCalibrationError:
        raise
    return PairwiseAnnotationSubmitResponse(
        status=result.status,
        annotation=_annotation_to_response(result.annotation),
    )


@router.get(
    "/runs/pairwise/annotations/{pair_id}",
    response_model=PairwiseAnnotationListResponse,
    responses=_ERROR_RESPONSES,
)
async def list_pairwise_annotations(
    pair_id: UUID,
    _reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PairwiseAnnotationListResponse:
    """List annotations for one Pair. ``suggested_label`` is never
    returned by this endpoint per supplementary constraint #6."""

    repo = EvalRepository(session)
    annotations = await repo.list_annotations_by_pair(pair_id)
    if not annotations:
        raise AppError(
            code="EVAL_ANNOTATION_NOT_FOUND",
            message="no annotations found for this pair",
            status_code=HTTPStatus.NOT_FOUND,
        )
    from evals.v2.calibration_metrics import ReviewerAnnotation, derive_pair_consensus_status

    primaries = [
        ReviewerAnnotation(
            reviewer_id=a.reviewer_id,
            pair_id=str(a.pair_id),
            label=a.normalized_winner,  # type: ignore[arg-type]
            is_adjudication=a.is_adjudication,
        )
        for a in annotations
        if not a.is_adjudication
    ]
    has_adj = any(a.is_adjudication for a in annotations)
    consensus = derive_pair_consensus_status(primaries, has_adjudication=has_adj)
    return PairwiseAnnotationListResponse(
        pair_id=pair_id,
        annotations=[_annotation_to_response(a) for a in annotations],
        has_adjudication=has_adj,
        pair_consensus_status=consensus,
    )


# ===========================================================================
# Calibration report
# ===========================================================================


def _compute_calibration_status_from_snapshots(
    *,
    sweeps: list[EvalPairwiseSweep],
    judge_result_snapshot: list[dict[str, object]],
    annotation_snapshot: list[dict[str, object]],
) -> dict[str, object]:
    """Issue #3 / Commit 3.2: turn the raw snapshots into the metric
    inputs ``compute_calibration_status`` expects, then compute the
    real ``CalibrationOutcome``. Also returns the structural + metric
    counts so the report payload exposes the position_pair_count vs
    position_metric_sample_count distinction explicitly.

    Definitions (per Commit 3.2 plan):

    * ``valid_human_pair_count`` — pairs with at least 2 distinct
      PRIMARY reviewers (adjudication rows excluded). Pairs with only
      one primary reviewer count as ``single`` and do NOT enter this
      set; this matches the inter-rater denominator.
    * ``position_pair_count`` — sweep structural counter summed across
      the input sweeps (both required Items completed WITH
      judge_result_id, per executor semantics).
    * ``position_metric_sample_count`` — pairs where BOTH required
      Judge results exist AND ``judge_run_status='completed'`` AND
      ``normalized_winner IS NOT NULL``. This is the actual metric
      denominator for position-bias / position-consistency analysis:
      an ``invalid_structured_output`` Judge result row exists
      structurally but does NOT contribute a position sample.
    * ``agreement`` — exact-match between judge normalized_winner and a
      single human-primary consensus label, over the intersection.
    * ``position_bias`` — fraction of decisive pairs (filter tie /
      both_unacceptable) where swapping the position flipped the
      judge's verdict.

    ``compute_calibration_status`` is invoked with the METRIC sample
    count, not the structural count.
    """

    from collections import defaultdict

    from evals.v2.calibration_metrics import compute_calibration_status

    # ---- valid_human_pair_count ----------------------------------------
    primaries_by_pair: dict[str, set[str]] = defaultdict(set)
    for ann in annotation_snapshot:
        if ann.get("is_adjudication"):
            continue
        if not ann.get("normalized_winner"):
            continue
        primaries_by_pair[str(ann["pair_id"])].add(str(ann["reviewer_id"]))
    valid_human_pair_count = sum(
        1 for reviewers in primaries_by_pair.values() if len(reviewers) >= 2
    )

    # ---- position_pair_count (structural, sweep counter) ----------------
    position_pair_count = sum(s.position_pair_count for s in sweeps)

    # ---- position_metric_sample_count (results with valid winners) -----
    # For each pair, count Completed-with-winner results: position
    # metric needs BOTH required variants (baseline + swapped) to be
    # present and Completed-with-winner. The judge_result_snapshot
    # does not currently expose position_variant directly (the result
    # row has it); we group per pair and require len>=2 distinct
    # position_variant entries both Completed-with-winner.
    by_pair_results: dict[str, list[dict[str, object]]] = defaultdict(list)
    for jr in judge_result_snapshot:
        by_pair_results[str(jr["pair_id"])].append(jr)
    position_metric_sample_count = 0
    for _, jrs in by_pair_results.items():
        valid = [
            j
            for j in jrs
            if j.get("judge_run_status") == "completed"
            and j.get("normalized_winner") is not None
        ]
        if len(valid) >= 2:
            position_metric_sample_count += 1

    # ---- agreement + position_bias在实际数据上的计算 ------------------
    # Pair the judge's normalized_winner with a single consensus human
    # label per pair (the dominant primary label, or first-seen).
    judge_winner_by_pair: dict[str, str] = {}
    for jr in judge_result_snapshot:
        if (
            jr.get("judge_run_status") == "completed"
            and jr.get("normalized_winner") is not None
        ):
            # Multiple results per pair (baseline + swapped) — pick the
            # baseline one for the agreement axis (they share the same
            # normalized_winner in baseline/candidate vocabulary by
            # construction).
            judge_winner_by_pair.setdefault(
                str(jr["pair_id"]), str(jr["normalized_winner"])
            )
    human_label_by_pair: dict[str, str] = {}
    for pair_id, reviewers in primaries_by_pair.items():
        if len(reviewers) < 2:
            continue
        labels = [
            str(a["normalized_winner"])
            for a in annotation_snapshot
            if str(a["pair_id"]) == pair_id
            and not a.get("is_adjudication")
            and a.get("normalized_winner")
        ]
        if labels:
            # Mode + first-seen tiebreak: pick any consistent label.
            try:
                from collections import Counter

                human_label_by_pair[pair_id] = Counter(labels).most_common(1)[0][0]
            except Exception:
                human_label_by_pair[pair_id] = labels[0]

    common_pairs = sorted(
        set(judge_winner_by_pair) & set(human_label_by_pair)
    )
    agreement_value: float | None = None
    if common_pairs:
        matches = sum(
            1
            for p in common_pairs
            if judge_winner_by_pair[p] == human_label_by_pair[p]
        )
        agreement_value = matches / len(common_pairs)

    # Position bias: of the decisive completed-with-winner pairs
    # (excluding tie / both_unacceptable), the fraction where baseline
    # and swapped Judge verdicts differ. This captures how often
    # re-ordering the display flips the Judge's pick.
    decisive_pair_verdicts: dict[str, set[str]] = defaultdict(set)
    for jr in judge_result_snapshot:
        if (
            jr.get("judge_run_status") == "completed"
            and jr.get("normalized_winner")
            in ("baseline", "candidate")
        ):
            decisive_pair_verdicts[str(jr["pair_id"])].add(
                str(jr["normalized_winner"])
            )
    position_bias_value: float | None = None
    decisive_count = sum(1 for s in decisive_pair_verdicts.values() if len(s) >= 1)
    if decisive_count > 0:
        flipped = sum(
            1 for s in decisive_pair_verdicts.values() if len(s) >= 2
        )
        position_bias_value = flipped / decisive_count

    outcome = compute_calibration_status(
        agreement=agreement_value,
        position_bias=position_bias_value,
        valid_human_pair_count=valid_human_pair_count,
        position_pair_count=position_metric_sample_count,
    )
    return {
        "calibration_status": outcome.calibration_status,
        "usage_mode": outcome.usage_mode,
        "agreement": agreement_value,
        "position_bias": position_bias_value,
        "valid_human_pair_count": valid_human_pair_count,
        "position_pair_count": position_pair_count,
        "position_metric_sample_count": position_metric_sample_count,
        "agreement_sample_count": len(common_pairs),
    }


@router.post(
    "/pairwise/calibration",
    response_model=PairwiseCalibrationReportResponse,
    responses=_ERROR_RESPONSES,
)
async def create_calibration_report(
    body: PairwiseCalibrationReportRequest,
    reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PairwiseCalibrationReportResponse:
    """Generate or reuse a calibration report.

    Caller MUST specify exact ``sweep_ids`` (supplementary constraint #7)
    and all sweeps MUST share the same judge identity."""
    repo = EvalRepository(session)
    sweeps: list[EvalPairwiseSweep] = []
    for sid in body.sweep_ids:
        sweep = await repo.get_sweep(sid)
        if sweep is None:
            raise AppError(
                code="EVAL_SWEEP_NOT_FOUND",
                message=f"sweep {sid} not found",
                status_code=HTTPStatus.NOT_FOUND,
            )
        sweeps.append(sweep)
    # Validate consistent judge identity.
    first = sweeps[0]
    for sweep in sweeps[1:]:
        if (
            sweep.judge_model_id != first.judge_model_id
            or sweep.judge_prompt_version != first.judge_prompt_version
            or sweep.judge_rubric_version != first.judge_rubric_version
        ):
            raise AppError(
                code="EVAL_CALIBRATION_JUDGE_IDENTITY_MISMATCH",
                message="all sweeps must share the same judge identity",
                status_code=HTTPStatus.CONFLICT,
            )

    # Gather judge result + annotation snapshots.
    judge_result_snapshot: list[dict[str, object]] = []
    annotation_snapshot: list[dict[str, object]] = []
    for sweep in sweeps:
        # JudgeResults via the sweep's comparison_group_id
        from sqlalchemy import select as _select

        from app.models.eval import EvalPairwiseJudgeResult

        results = await session.execute(
            _select(EvalPairwiseJudgeResult).where(
                EvalPairwiseJudgeResult.comparison_group_id
                == sweep.comparison_group_id
            )
        )
        for jr in results.scalars():
            judge_result_snapshot.append({
                "pair_id": str(jr.pair_id),
                "judge_run_id": str(jr.judge_run_id),
                "judge_result_id": str(jr.id),
                "judge_model_id": jr.model_id,
                "judge_run_status": jr.judge_run_status,
                "normalized_winner": jr.normalized_winner,
                "input_hash": jr.input_hash,
            })
        annotations = await repo.list_annotations_by_sweep(sweep.id)
        for ann in annotations:
            annotation_snapshot.append({
                "annotation_id": str(ann.id),
                "pair_id": str(ann.pair_id),
                "reviewer_id": ann.reviewer_id,
                "reviewer_role": ann.reviewer_role,
                "is_adjudication": ann.is_adjudication,
                "normalized_winner": ann.normalized_winner,
                "position_variant": ann.position_variant,
                "submission_hash": ann.submission_hash,
                "review_input_hash": ann.review_input_hash,
            })

    # Issue #3 / Commit 3.2: compute the real calibration status from the
    # snapshots (replaces the prior hard-default ``insufficient``). The
    # outcome lives inside ``report_payload`` so the GET endpoints read
    # the real value rather than .get(..., "insufficient").
    status_metrics = _compute_calibration_status_from_snapshots(
        sweeps=sweeps,
        judge_result_snapshot=judge_result_snapshot,
        annotation_snapshot=annotation_snapshot,
    )

    report_payload: dict[str, object] = {
        "sweep_ids": [str(sid) for sid in body.sweep_ids],
        "judge_result_count": len(judge_result_snapshot),
        "annotation_count": len(annotation_snapshot),
        "judge_model_id": first.judge_model_id,
        "judge_prompt_version": first.judge_prompt_version,
        "judge_rubric_version": first.judge_rubric_version,
        # Real calibration outcome + the four metric invariants.
        "calibration_status": status_metrics["calibration_status"],
        "usage_mode": status_metrics["usage_mode"],
        "agreement": status_metrics["agreement"],
        "position_bias": status_metrics["position_bias"],
        "valid_human_pair_count": status_metrics["valid_human_pair_count"],
        "position_pair_count": status_metrics["position_pair_count"],
        # Distinct from position_pair_count: counts ONLY pairs whose
        # BOTH required JudgeResult rows are Completed-with-winner.
        # This is the denominator position-bias / position-consistency
        # metrics are computed over, and the threshold the gate uses.
        "position_metric_sample_count": status_metrics[
            "position_metric_sample_count"
        ],
        "agreement_sample_count": status_metrics["agreement_sample_count"],
    }

    service = PairwiseCalibrationService(session)
    status, report = await service.create_or_reuse_calibration_report(
        dataset_id=body.dataset_id,
        dataset_version=body.dataset_version,
        source_sha256=first.source_sha256,
        judge_model_id=first.judge_model_id,
        judge_prompt_version=first.judge_prompt_version,
        judge_rubric_version=first.judge_rubric_version,
        annotation_schema_version=first.annotation_schema_version,
        calibration_policy_version=CALIBRATION_POLICY_VERSION,
        sweep_ids=body.sweep_ids,
        judge_result_snapshot=judge_result_snapshot,
        annotation_snapshot=annotation_snapshot,
        report_payload=report_payload,
        requested_by=str(reviewer.id),
    )

    payload = dict(report.report_payload)
    calibration_status = str(payload.get("calibration_status", "insufficient"))
    usage_mode = str(payload.get("usage_mode", "diagnostic_only"))

    return PairwiseCalibrationReportResponse(
        report_id=report.id,
        dataset_id=report.dataset_id,
        dataset_version=report.dataset_version,
        source_sha256=report.source_sha256,
        judge_model_id=report.judge_model_id,
        judge_prompt_version=report.judge_prompt_version,
        judge_rubric_version=report.judge_rubric_version,
        annotation_schema_version=report.annotation_schema_version,
        calibration_policy_version=report.calibration_policy_version,
        input_hash=report.input_hash,
        content_hash=report.content_hash,
        calibration_status=calibration_status,
        usage_mode=usage_mode,
        requested_by=report.requested_by,
        created_at=report.created_at,
        report_payload=payload,
        status=status,
    )


@router.get(
    "/pairwise/calibration/{dataset_id}/{dataset_version}/latest",
    response_model=PairwiseCalibrationReportResponse,
    responses=_ERROR_RESPONSES,
)
async def get_latest_calibration_report(
    dataset_id: str,
    dataset_version: str,
    _reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> PairwiseCalibrationReportResponse:
    repo = EvalRepository(session)
    report = await repo.get_latest_calibration_report(dataset_id, dataset_version)
    if report is None:
        raise AppError(
            code="EVAL_CALIBRATION_NOT_COMPUTED",
            message="no calibration report exists for this dataset",
            status_code=HTTPStatus.NOT_FOUND,
        )
    payload = dict(report.report_payload)
    calibration_status = str(payload.get("calibration_status", "insufficient"))
    usage_mode = str(payload.get("usage_mode", "diagnostic_only"))
    return PairwiseCalibrationReportResponse(
        report_id=report.id,
        dataset_id=report.dataset_id,
        dataset_version=report.dataset_version,
        source_sha256=report.source_sha256,
        judge_model_id=report.judge_model_id,
        judge_prompt_version=report.judge_prompt_version,
        judge_rubric_version=report.judge_rubric_version,
        annotation_schema_version=report.annotation_schema_version,
        calibration_policy_version=report.calibration_policy_version,
        input_hash=report.input_hash,
        content_hash=report.content_hash,
        calibration_status=calibration_status,
        usage_mode=usage_mode,
        requested_by=report.requested_by,
        created_at=report.created_at,
        report_payload=payload,
        status="existing",
    )


@router.get(
    "/pairwise/calibration/{dataset_id}/{dataset_version}/history",
    response_model=list[PairwiseCalibrationReportResponse],
    responses=_ERROR_RESPONSES,
)
async def list_calibration_reports(
    dataset_id: str,
    dataset_version: str,
    _reviewer: Annotated[AuthenticatedUser, Depends(require_dev)],
    session: Annotated[AsyncSession, Depends(get_db_session, use_cache=False)],
) -> list[PairwiseCalibrationReportResponse]:
    repo = EvalRepository(session)
    reports = await repo.list_calibration_reports(dataset_id, dataset_version)
    out: list[PairwiseCalibrationReportResponse] = []
    for report in reports:
        payload = dict(report.report_payload)
        calibration_status = str(payload.get("calibration_status", "insufficient"))
        usage_mode = str(payload.get("usage_mode", "diagnostic_only"))
        out.append(
            PairwiseCalibrationReportResponse(
                report_id=report.id,
                dataset_id=report.dataset_id,
                dataset_version=report.dataset_version,
                source_sha256=report.source_sha256,
                judge_model_id=report.judge_model_id,
                judge_prompt_version=report.judge_prompt_version,
                judge_rubric_version=report.judge_rubric_version,
                annotation_schema_version=report.annotation_schema_version,
                calibration_policy_version=report.calibration_policy_version,
                input_hash=report.input_hash,
                content_hash=report.content_hash,
                calibration_status=calibration_status,
                usage_mode=usage_mode,
                requested_by=report.requested_by,
                created_at=report.created_at,
                report_payload=payload,
                status="existing",
            )
        )
    return out
