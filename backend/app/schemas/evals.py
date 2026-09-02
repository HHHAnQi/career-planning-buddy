"""Strict request/response schemas for the Eval V2 HTTP control plane."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import StrictModel


class EvalRunCreateRequest(StrictModel):
    """Body of POST /api/v1/eval/runs.

    The server loads the named dataset (mirroring the CLI) instead of asking
    the caller to ship a fully-validated ``DatasetBundle`` over the wire.
    """

    dataset: Literal["stage5", "runtime-smoke"] = "stage5"
    cases: list[str] | None = Field(
        default=None,
        description="Subset of case_ids to keep (default: all cases in the dataset).",
    )
    provider_mode: Literal["mock", "fixture", "live"] | None = None
    trial_count: int = Field(default=1, ge=1, le=100)
    grade: bool = True
    run_type: Literal["evaluation", "fixture_replay"] = "evaluation"
    fixture_source_experiment_id: UUID | None = None
    baseline_experiment_id: UUID | None = None
    agent_variant: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Stage B-1a-lite experiment-level agent variant.",
    )

    @model_validator(mode="after")
    def validate_fixture_source(self) -> "EvalRunCreateRequest":
        if self.run_type == "fixture_replay" and self.fixture_source_experiment_id is None:
            raise ValueError("fixture_replay requires fixture_source_experiment_id")
        if self.run_type != "fixture_replay" and self.fixture_source_experiment_id is not None:
            raise ValueError(
                "fixture_source_experiment_id is only valid for fixture_replay"
            )
        return self


class EvalRunCreatedResponse(StrictModel):
    experiment_id: UUID
    status: Literal["draft"]
    status_url: str
    report_url: str


class TrialStatusSummary(StrictModel):
    trial_id: UUID
    case_id: str
    status: str
    run_status: str | None
    result_kind: str | None
    error_code: str | None
    fixture_source_trial_id: UUID | None = None


class EvalRunStatusResponse(StrictModel):
    experiment_id: UUID
    status: Literal["draft", "running", "completed", "failed", "cancelled"]
    dataset_id: str
    trial_count: int
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    trials: list[TrialStatusSummary]
    execution_mode: str
    dataset_version: str
    variant_role: Literal["baseline", "candidate"]
    baseline_experiment_id: UUID | None
    agent_variant: str | None
    git_commit: str
    graph_version: str
    feature_stage: int
    prompt_version: str
    model_version: str
    tool_version: str
    context_version: str
    memory_version: str
    search_version: str
    eval_harness_version: str


class EvalRunReportResponse(StrictModel):
    """Strongly-typed envelope for ``ExperimentReport.to_dict()`` (PR-6)."""

    experiment_id: UUID
    experiment_status: str
    trial_count: int
    completed_trial_count: int
    scored_trial_count: int
    hard_gate_pass_fraction: float
    any_score_generated: bool
    trials: list[dict[str, object]]
    # PR-9a: per-case + per-experiment statistics. Both default-empty so
    # pre-PR-9a consumers continue to deserialise cleanly.
    case_stats: dict[str, dict[str, object]] = Field(default_factory=dict)
    experiment_stats: dict[str, object] | None = None
    failure_counts: dict[str, int] = Field(default_factory=dict)
    # PR-9b: report revision (content-hash driven, not call-driven) plus
    # the fact-record of any implised cancel request.
    revision: int = 0
    cancel_requested_at: datetime | None = None


class EvalRunListItem(StrictModel):
    """One row in the paginated Experiment listing."""

    experiment_id: UUID
    status: str
    execution_mode: str
    dataset_id: str
    dataset_version: str
    trial_count: int
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    variant_role: Literal["baseline", "candidate"]
    baseline_experiment_id: UUID | None
    agent_variant: str | None
    git_commit: str
    graph_version: str
    feature_stage: int
    prompt_version: str
    model_version: str
    tool_version: str
    context_version: str
    memory_version: str
    search_version: str
    eval_harness_version: str


class EvalRunListResponse(StrictModel):
    """Paginated response for GET /api/v1/eval/runs."""

    items: list[EvalRunListItem]
    next_offset: int | None = None


class EvalRunProgressResponse(StrictModel):
    """Lightweight progress view distinct from the heavy report payload."""

    experiment_id: UUID
    status: str
    trial_count: int
    completed_count: int
    running_count: int
    pending_count: int
    failed_count: int
    cancelled_count: int
    timed_out_count: int
    in_flight_trial_ids: list[UUID]
    cancel_requested_at: datetime | None
    estimated_progress: float
    # Best-effort "last seen event"-style pointers. Optional: not surfaced
    # when no agent_step row has emitted yet.
    last_event_type: str | None = None
    last_event_at: datetime | None = None


class EvalRunCancelResponse(StrictModel):
    """Response body for POST /api/v1/eval/runs/{id}/cancel.

    ``status`` is the Experiment's current status at the time the request
    was processed; it is NOT promoted to ``cancelled`` synchronously.
    ``cancel_requested`` is False if the Experiment was already terminal
    (no fresh timestamp stamped, request is a no-op).
    """

    experiment_id: UUID
    status: str
    cancel_requested: bool
    cancel_requested_at: datetime | None = None


# ===========================================================================
# PR-9c.2 Pairwise Calibration Workflow
# ===========================================================================


class PairwiseRunRequest(StrictModel):
    """Body of POST /api/v1/eval/runs/{baseline_exp}/pairwise/run."""

    candidate_experiment_id: UUID
    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_version: str = Field(min_length=1, max_length=32)
    fixture_mapping: dict[str, dict[str, object]] | None = Field(
        default=None,
        description=(
            "Optional pair_hash → PairwiseJudgeOutput (as serialized dict) "
            "mapping for fixture-mode smoke runs. MUST be omitted (or null) "
            "for production sweeps. Commit 3.3."
        ),
    )


class PairwiseRunStatusResponse(StrictModel):
    sweep_id: UUID
    comparison_group_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    dataset_id: str
    dataset_version: str
    source_sha256: str
    judge_model_id: str
    judge_prompt_version: str
    judge_rubric_version: str
    annotation_schema_version: str
    requested_pair_count: int
    requested_judge_run_count: int
    completed_judge_run_count: int
    failed_judge_run_count: int
    completed_pair_count: int
    position_pair_count: int
    requested_by: str
    started_at: datetime
    cancel_requested_at: datetime | None = None
    terminal_at: datetime | None = None


class PairwiseRunCancelResponse(StrictModel):
    sweep_id: UUID
    cancel_requested: bool
    cancel_requested_at: datetime | None = None


class PairwiseAnnotationSubmitRequest(StrictModel):
    """Body of POST /api/v1/eval/runs/pairwise/annotations.

    Server-authoritative fields NOT accepted here (per supplementary
    constraint #6):

    * reviewer_id — derived from JWT subject
    * position_variant — derived by the service from (pair, reviewer,
      annotation_schema_version) via ``derive_position_variant``
    * normalized_winner / normalized_dimensions — recomputed from raw +
      position_variant
    * display_a_trial_id / display_b_trial_id — recomputed from the sweep
      item's frozen review surface

    The reviewer only submits the raw display-side verdicts.
    """

    pair_id: UUID
    sweep_id: UUID
    raw_winner: Literal["a", "b", "tie", "both_unacceptable"]
    raw_dimension_verdicts: dict[
        Literal[
            "actionability",
            "alignment",
            "personalization",
            "clarity",
            "consistency",
        ],
        Literal["a", "b", "tie", "both_unacceptable"],
    ]
    rationale: str | None = None
    is_adjudication: bool = False
    review_token: str = Field(
        description=(
            "REQUIRED (Commit 3.2 issue #1). Tamper-protection token "
            "returned by the GET review-surface endpoint. The server "
            "re-derives it from (pair_id, reviewer_id, "
            "frozen_review_surface_sha256) and rejects on mismatch or "
            "absence. A reviewer cannot submit a primary or "
            "adjudication annotation without first fetching the "
            "server-rendered Review Surface — closing the server-"
            "authoritative blinded-evaluation invariant."
        ),
    )


class PairwiseAnnotationResponse(StrictModel):
    """Single annotation response."""

    annotation_id: UUID
    pair_id: UUID
    sweep_id: UUID
    reviewer_id: str
    reviewer_role: Literal["primary", "adjudicator"]
    is_adjudication: bool
    raw_winner: Literal["a", "b", "tie", "both_unacceptable"]
    normalized_winner: Literal[
        "baseline", "candidate", "tie", "both_unacceptable"
    ]
    position_variant: Literal["baseline", "swapped"]
    annotation_schema_version: str
    rubric_version: str
    judge_prompt_version: str
    judge_model_id: str
    frozen_review_surface_sha256: str
    created_at: datetime
    rationale: str | None = None


class PairwiseAnnotationSubmitResponse(StrictModel):
    """Wrapped response carrying the HTTP status code difference (200 vs
    201). ``status`` tells the caller whether the row was newly inserted
    or already present."""

    status: Literal["created", "existing"]
    annotation: PairwiseAnnotationResponse


class PairwiseAnnotationListResponse(StrictModel):
    pair_id: UUID
    annotations: list[PairwiseAnnotationResponse]
    has_adjudication: bool
    pair_consensus_status: str


# ----------------------------------------------------- review-surface (3.1)


class PairwiseReviewSurfaceResponse(StrictModel):
    """Server-rendered blinded Review Surface returned by
    ``GET /api/v1/eval/runs/pairwise/pairs/{pair_id}/review-surface``.

    Reviewer-authoritative fields EXCLUDED by design (issue #4):

    * ``pair_hash`` — would let the reviewer correlate repeated surfaces
      or guess at sibling identity;
    * ``display_*_trial_id`` / baseline_or_candidate role markers —
      reveals which side is which;
    * model / provider identity, run cost / latency, automatic scores;
    * ``suggested_label`` or any Judge hint.

    What the reviewer DOES see: ``display_a`` / ``display_b`` (request +
    plan projections), the deterministic ``position_variant`` they were
    assigned, the rubric they should apply, and a non-secret
    ``review_token`` that ties a subsequent POST annotation back to this
    exact surface (``sha256(pair_id | reviewer_id |
    frozen_review_surface_sha256)[:16]``).
    """

    pair_id: UUID
    sweep_id: UUID
    case_id: str
    review_surface_version: str
    annotation_schema_version: str
    rubric_version: str
    position_variant: Literal["baseline", "swapped"]
    rubric: list[dict[str, object]]
    display_a: dict[str, object]
    display_b: dict[str, object]
    frozen_review_surface_sha256: str
    review_token: str


# ---------------------------------------------------------------- calibration


class PairwiseCalibrationReportRequest(StrictModel):
    """Explicit sweep identity gate per supplementary constraint #7:
    the caller MUST pass the exact list of sweep_ids whose JudgeResults
    + annotations form the report's input set."""

    dataset_id: str = Field(min_length=1, max_length=128)
    dataset_version: str = Field(min_length=1, max_length=32)
    sweep_ids: list[UUID] = Field(min_length=1)


class PairwiseCalibrationReportResponse(StrictModel):
    report_id: UUID
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
    calibration_status: Literal[
        "passing", "failing", "insufficient"
    ]
    usage_mode: Literal["diagnostic_only", "gate_eligible"]
    requested_by: str
    created_at: datetime
    report_payload: dict[str, object]
    status: Literal["created", "existing"]
