"""Strict developer Trace, Replay, and Eval API contracts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import StrictModel


class DevRunSummary(StrictModel):
    run_id: UUID
    replay_of_run_id: UUID | None
    user_ref: str
    status: str
    result_kind: str | None
    resolved_intent: str | None
    graph_version: str
    model_id: str | None
    total_tokens_in: int
    total_tokens_out: int
    total_cost_cny: Decimal
    total_latency_ms: int
    fallback_reason: str | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class DevRunListResponse(StrictModel):
    items: list[DevRunSummary]
    next_cursor: UUID | None


class DevStepTrace(StrictModel):
    sequence: int
    node_name: str
    attempt: int
    status: str
    prompt_version: str | None
    model_id: str | None
    tokens_in: int
    tokens_out: int
    latency_ms: int
    input_hash: str | None
    output_hash: str | None
    trace_data: object
    error_code: str | None


class DevToolTrace(StrictModel):
    tool_call_id: UUID
    step_id: UUID
    tool_name: str
    contract_version: str
    round: int
    args: object
    args_hash: str
    result: object | None
    result_hash: str | None
    provider: str | None
    latency_ms: int
    success: bool
    error_code: str | None


class DevEventTrace(StrictModel):
    sequence: int
    event_type: str
    payload: object
    created_at: datetime


class DevSnapshot(StrictModel):
    data: object
    sha256: str


class TerminalInvariant(StrictModel):
    terminal_count: int
    terminal_is_last: bool
    valid: bool


class DevRunDetail(StrictModel):
    run: DevRunSummary
    request_text: str
    input_snapshot: DevSnapshot | None
    config_snapshot: DevSnapshot
    result: object | None
    steps: list[DevStepTrace]
    tools: list[DevToolTrace]
    events: list[DevEventTrace]
    terminal_invariant: TerminalInvariant


class ReplayRequest(StrictModel):
    # Stage 5 compatibility. Its presence selects the explicitly labelled trace clone;
    # executable Replay callers use ``mode`` below.
    tool_mode: Literal["fixture", "live"] | None = None
    mode: Literal["exact_fixture_replay", "candidate_comparison"] = "exact_fixture_replay"
    target_runtime_bundle_id: UUID | None = None


class ReplayResponse(StrictModel):
    run_id: UUID
    replay_of_run_id: UUID
    status: str
    deterministic: bool
    execution_kind: Literal[
        "legacy_trace_clone", "replay_v2", "exact_fixture_replay", "candidate_comparison"
    ]


class ReplayDiff(StrictModel):
    source_run_id: UUID
    replay_run_id: UUID
    source_status: str
    replay_status: str
    input_snapshot_equal: bool
    semantic_equal: bool
    source_result_sha256: str | None
    replay_result_sha256: str | None
    changed_fields: list[str]
    comparison_version: str = "resume-semantic-diff-v2"
    context_diff: object
    tool_diff: object
    claim_diff: object
    validation_diff: object
    usage_diff: object


class EvalDatasetSummary(StrictModel):
    dataset_id: str
    case_count: int = Field(ge=1)
    description: str


class EvalDatasetListResponse(StrictModel):
    items: list[EvalDatasetSummary]


class EvalStartRequest(StrictModel):
    dataset_id: str = "stage5-v1"
    case_limit: int | None = Field(default=None, ge=1, le=30)


class EvalStartResponse(StrictModel):
    experiment_id: str
    status: Literal["completed"]


class EvalExperimentResponse(StrictModel):
    experiment_id: str
    status: Literal["completed", "not_found"]
    report: object | None


class UsageTotals(StrictModel):
    """Window-wide run aggregates for cost and latency reporting."""

    run_count: int
    completed_count: int
    degraded_count: int
    failed_count: int
    fallback_count: int
    total_cost_cny: Decimal = Field(default=Decimal("0"))
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    avg_cost_per_run_cny: Decimal = Field(default=Decimal("0"))
    latency_p50_ms: int = 0
    latency_p95_ms: int = 0
    latency_max_ms: int = 0


class UsageGraphBreakdown(StrictModel):
    """Per (graph_version, model_id) slice of the same window."""

    graph_version: str
    model_id: str | None
    run_count: int
    total_cost_cny: Decimal
    avg_latency_ms: int


class UsageDailyPoint(StrictModel):
    """One UTC day of run activity for trend charts."""

    date: date
    run_count: int
    total_cost_cny: Decimal


class UsageProviderKind(StrictModel):
    """Provider-call health per provider kind in the window."""

    provider_kind: str
    call_count: int
    error_count: int
    avg_latency_ms: int


class UsageReportResponse(StrictModel):
    window_days: int = Field(ge=1, le=365)
    generated_at: datetime
    totals: UsageTotals
    graphs: list[UsageGraphBreakdown]
    daily: list[UsageDailyPoint]
    provider_kinds: list[UsageProviderKind]


class RepairStat(StrictModel):
    """One repair mechanism's outcome over the reporting window."""

    kind: Literal["format_repair", "business_repair"]
    # Repair LLM attempts actually made (persisted steps with the repair
    # prompt version).
    triggered: int
    succeeded: int
    # Runs that still fell back after a repair attempt.
    failed_after_attempt: int
    # Runs where repair was requested but declined by the budget guard
    # (no LLM attempt was made).
    declined_by_budget: int
    success_rate: float = Field(ge=0.0, le=1.0)


class FallbackReasonCount(StrictModel):
    reason: str
    count: int


class RepairReportResponse(StrictModel):
    window_days: int = Field(ge=1, le=365)
    generated_at: datetime
    repairs: list[RepairStat]
    fallback_reasons: list[FallbackReasonCount]
