"""Strict Agent Run, runtime snapshot, and graph data contracts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, TypedDict
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.base import StrictModel
from app.schemas.enums import (
    CareerStage,
    GoalType,
    PlanStatus,
    ReplanMode,
    RunIntent,
    RunKind,
    RunResultKind,
    RunStatus,
    SkillLevel,
    TaskStatus,
    TaskType,
)
from app.schemas.interviews import InterviewReportResultSummary, InterviewTurnResultSummary
from app.schemas.resumes import ResumeAssessmentResultSummary


class AgentRunCreatedResponse(StrictModel):
    run_id: UUID
    status: RunStatus
    events_url: str


class AgentRunCancelRequest(StrictModel):
    reason: str = Field(default="user_abort", min_length=1, max_length=64)


class AgentRunCancelResponse(StrictModel):
    run_id: UUID
    status: RunStatus
    cancel_requested: bool


class PlanResultSummary(StrictModel):
    plan_id: UUID
    status: Literal["generated"]
    plan_date: date
    horizon_end: date
    summary: str = Field(min_length=1, max_length=500)
    # Historical Runs may predate the strict seven-day generation contract.
    task_count: int = Field(ge=1, le=7)


class SuggestedAction(StrictModel):
    action: Literal[
        "complete_profile",
        "create_plan",
        "continue_plan",
        "adjust_plan",
        "view_current_plan",
        "view_today_tasks",
    ]
    label: str = Field(min_length=1, max_length=80)
    target_route: Literal["/settings/profile", "/journey", "/today", "/reviews"]


class ClarificationRequest(StrictModel):
    questions: list[str] = Field(min_length=1, max_length=3)
    slot_names: list[str] = Field(min_length=1, max_length=3)
    hint_options: dict[str, list[str]]
    reason: Literal["profile_incomplete", "unsupported_intent", "intent_uncertain"]
    message: str = Field(default="还需要补充一点信息。", min_length=1, max_length=500)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list, max_length=3)
    target_route: Literal["/settings/profile", "/journey", "/today"] | None = None


class NavigationResult(StrictModel):
    action: Literal["view_current_plan", "view_today_tasks"]
    label: str = Field(min_length=1, max_length=80)
    target_route: Literal["/journey", "/today"]
    message: str = Field(min_length=1, max_length=500)


class SafeResponse(StrictModel):
    message: str = Field(min_length=1, max_length=1000)
    resource_ids: list[str] = Field(default_factory=list, max_length=10)
    disclaimer: str = Field(min_length=1, max_length=500)


TerminalResult = (
    PlanResultSummary
    | ClarificationRequest
    | SafeResponse
    | NavigationResult
    | InterviewTurnResultSummary
    | InterviewReportResultSummary
    | ResumeAssessmentResultSummary
)
RunUserStatus = Literal[
    "queued",
    "generating",
    "recovering",
    "stopping",
    "ready",
    "action_required",
    "failed",
    "cancelled",
]


class AgentRunResponse(StrictModel):
    run_id: UUID
    run_kind: RunKind
    status: RunStatus
    user_status: RunUserStatus
    status_message: str = Field(min_length=1, max_length=200)
    resolved_intent: RunIntent | None
    replan_mode: ReplanMode | None
    result_kind: RunResultKind | None
    result: TerminalResult | None
    final_plan_id: UUID | None
    fallback_reason: str | None
    error_code: str | None
    risk_category: str | None
    total_tokens_in: int = Field(ge=0)
    total_tokens_out: int = Field(ge=0)
    total_cost_cny: Decimal = Field(ge=0)
    total_latency_ms: int = Field(ge=0)
    created_at: datetime
    finished_at: datetime | None
    cancel_requested_at: datetime | None


class RuntimeConfigSnapshot(StrictModel):
    graph_version: str
    # Keep prior snapshots readable while new runs are pinned to Stage 6B.
    feature_stage: Literal[3, 4, 5, 6] = 6
    available_tools: list[str] = Field(default_factory=list, max_length=3)
    provider: Literal["mock", "openai", "zhipu", "deepseek", "openai_compatible"] = "mock"
    model_alias: str
    prompt_versions: dict[str, str]
    max_llm_calls: int = Field(ge=1, le=7)
    max_tool_rounds: int = Field(ge=0, le=2)
    max_tool_calls: int = Field(ge=0, le=4)
    max_total_tokens: int = Field(ge=1)
    max_input_tokens_per_call: int = Field(ge=1)
    max_output_tokens_per_call: int = Field(ge=1)
    deadline_seconds: int = Field(ge=1)
    node_timeouts_seconds: dict[str, float]
    memory_semantic_retrieval_enabled: bool = True
    memory_disabled: bool = False
    memory_retrieval_limit: int = Field(default=8, ge=1, le=20)
    memory_context_max_items: int = Field(default=5, ge=1, le=5)
    memory_context_max_chars: int = Field(default=1200, ge=100, le=10000)
    memory_min_similarity: float = Field(default=0.35, ge=0, le=1)
    memory_recency_half_life_days: int = Field(default=14, ge=1, le=365)

    # PR-8 counterfactual knobs. All default to "no-op / legacy" so pre-PR-8
    # AgentRuns continue to validate. The graph's ContextBuilder reads them
    # to drive Memory / Context budget ablations.
    exclude_memory_categories: list[str] = Field(default_factory=list, max_length=8)
    context_recent_tasks_budget: int = Field(default=5, ge=0, le=30)
    context_recent_reviews_budget: int = Field(default=2, ge=0, le=10)
    tool_required: bool = False
    expected_citations: list[str] = Field(default_factory=list, max_length=20)
    execution_kind: Literal[
        "live", "replay_v2", "exact_fixture_replay", "candidate_comparison"
    ] = "live"
    replay_tool_mode: Literal["fixture", "live"] | None = None


class RunRequestSnapshot(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    hint_intent: Literal["create_plan", "replan"] | None
    goal_type_override: GoalType | None
    source_plan_id: UUID | None
    source_review_id: UUID | None = None
    source_interview_report_session_id: UUID | None = None


class ProfileContext(StrictModel):
    user_id: UUID
    version: int = Field(ge=1)
    goal_type: GoalType
    stage: CareerStage
    time_budget_minutes: int = Field(ge=15, le=480)
    skill_level: SkillLevel
    skill_summary: str | None = Field(default=None, max_length=2000)
    start_date: date | None = None
    deadline: date | None = None


class PlanningWindow(StrictModel):
    planning_date: date
    horizon_start: date
    horizon_end: date
    horizon_weeks: int = Field(ge=1, le=8)


class PlanFocusContext(StrictModel):
    week_index: int = Field(ge=1, le=8)
    focus: str = Field(min_length=1, max_length=160)
    success_signal: str = Field(min_length=1, max_length=200)


class PlanContext(StrictModel):
    plan_id: UUID
    version: int = Field(ge=1)
    status: PlanStatus
    plan_date: date
    horizon_start: date
    horizon_end: date
    overall_direction: str = Field(min_length=1, max_length=500)
    weekly_focus: list[PlanFocusContext] = Field(min_length=1, max_length=8)


class TaskContext(StrictModel):
    task_id: UUID
    state: TaskStatus
    title: str
    deliverable: str
    scheduled_date: date
    abandoned_reason: str | None = None
    abandoned_reason_text: str | None = None


class ReviewContext(StrictModel):
    review_id: UUID
    review_date: date
    blockers: str | None = None
    adjustment_request: str | None = None
    free_text: str | None = None
    replan_reason: str | None = None


class MemoryContext(StrictModel):
    memory_id: UUID
    version: int = Field(ge=1)
    memory_type: str
    summary: str = Field(min_length=1, max_length=500)


class EvidenceCatalogItem(StrictModel):
    kind: Literal["memory", "experience_atom", "search_source", "rag_document_chunk"]
    id: UUID
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=2000)
    reliability: float = Field(ge=0, le=1)


class PlanningContext(StrictModel):
    profile: ProfileContext
    planning_window: PlanningWindow
    source_plan_id: UUID | None = None
    source_plan_version: int | None = None
    source_plan: PlanContext | None = None
    source_review: ReviewContext | None = None
    source_interview_report_session_id: UUID | None = None
    interview_training_actions: list[str] = Field(default_factory=list, max_length=3)
    recent_tasks: list[TaskContext] = Field(default_factory=list, max_length=30)
    recent_reviews: list[ReviewContext] = Field(default_factory=list, max_length=7)
    completed_facts: list[str] = Field(default_factory=list, max_length=20)
    blockers: list[str] = Field(default_factory=list, max_length=10)
    pinned_memories: list[MemoryContext] = Field(default_factory=list, max_length=5)
    task_history_summary: str | None = None
    review_history_summary: str | None = None
    timezone: str = "UTC"
    time_budget_minutes: int = Field(ge=15, le=480)
    token_estimate: int = Field(ge=0)


class RunInputSnapshot(StrictModel):
    profile: ProfileContext
    planning_window: PlanningWindow
    source_plan_id: UUID | None
    source_plan_version: int | None
    source_plan: PlanContext | None = None
    source_review: ReviewContext | None = None
    source_interview_report_session_id: UUID | None = None
    interview_training_actions: list[str] = Field(default_factory=list, max_length=3)
    recent_tasks: list[TaskContext] = Field(default_factory=list, max_length=30)
    recent_reviews: list[ReviewContext] = Field(default_factory=list, max_length=7)
    completed_facts: list[str] = Field(default_factory=list, max_length=20)
    blockers: list[str] = Field(default_factory=list, max_length=10)
    pinned_memories: list[MemoryContext] = Field(default_factory=list, max_length=5)
    task_history_summary: str | None = None
    review_history_summary: str | None = None
    recent_task_ids: list[UUID] = Field(default_factory=list, max_length=30)
    recent_review_ids: list[UUID] = Field(default_factory=list, max_length=7)
    memory_versions: dict[str, int] = Field(default_factory=dict)
    timezone: str
    time_budget_minutes: int = Field(ge=15, le=480)


class RiskResult(StrictModel):
    level: Literal["none", "high"]
    category: Literal["self_harm", "mental_health", "legal", "financial", "other"] | None
    method: Literal["rule", "classifier", "rule_and_classifier"]
    matched_rule_ids: list[str]
    confidence: float | None = Field(default=None, ge=0, le=1)


class IntentResult(StrictModel):
    intent: RunIntent
    replan_mode: ReplanMode
    confidence: float = Field(ge=0, le=1)
    confidence_band: Literal["high", "medium", "low"] = "high"
    router_version: str = Field(default="intent-rule-v3", min_length=1, max_length=64)
    matched_rule_ids: list[str] = Field(default_factory=list, max_length=20)
    ambiguity_reasons: list[str] = Field(default_factory=list, max_length=10)
    missing_slots: list[Literal["goal_type", "stage", "time_budget_minutes", "skill_level"]]
    effective_goal_type: GoalType | None
    requested_horizon_weeks: int | None = Field(default=None, ge=1, le=8)
    requires_fresh_information: bool
    # Deterministic signal: the user's message references their past
    # execution context — the planning agent should call memory_lookup.
    references_past_context: bool = False
    method: Literal["rule", "model", "rule_fallback"]
    navigation_action: Literal["view_current_plan", "view_today_tasks"] | None = None
    navigation_target: Literal["/journey", "/today"] | None = None


class WeeklyFocusCandidate(StrictModel):
    week_index: int = Field(ge=1, le=8)
    focus: str = Field(min_length=1, max_length=160)
    success_signal: str = Field(min_length=1, max_length=200)


class EvidenceRef(StrictModel):
    kind: Literal["memory", "experience_atom", "search_source", "rag_document_chunk"]
    id: UUID


class EvidenceVisibility(StrictModel):
    """Evidence refs rendered into one candidate-producing Provider call."""

    call_id: str = Field(min_length=1, max_length=200)
    catalog_hash: str = Field(min_length=64, max_length=64)
    visible_refs: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    truncated_refs: list[EvidenceRef] = Field(default_factory=list, max_length=100)


class TaskCandidate(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    task_type: TaskType
    scheduled_date: date
    starter_action: str = Field(min_length=1, max_length=240)
    deliverable: str = Field(min_length=1, max_length=240)
    estimated_minutes: int = Field(ge=5, le=480)
    rationale: str | None = Field(default=None, max_length=500)


class PlanCandidate(StrictModel):
    plan_date: date
    horizon_start: date
    horizon_end: date
    overall_direction: str = Field(min_length=1, max_length=500)
    weekly_focus: list[WeeklyFocusCandidate] = Field(min_length=1, max_length=8)
    summary: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=2000)
    adjustment_reason: str | None = Field(default=None, max_length=1000)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    tasks: list[TaskCandidate] = Field(min_length=1, max_length=7)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=10)

    @field_validator("weekly_focus")
    @classmethod
    def require_contiguous_weeks(
        cls, values: list[WeeklyFocusCandidate]
    ) -> list[WeeklyFocusCandidate]:
        if [item.week_index for item in values] != list(range(1, len(values) + 1)):
            raise ValueError("weekly focus indexes must be contiguous from one")
        return values


class ValidationCheck(StrictModel):
    code: str
    passed: bool
    message: str
    task_index: int | None = None


class ValidationReport(StrictModel):
    passed: bool
    checks: list[ValidationCheck]
    repair_instructions: list[str]


class CompanionMessageCandidate(StrictModel):
    trigger_tag: Literal["plan_ready"] = "plan_ready"
    message: str = Field(min_length=1, max_length=500)
    template_version: str


class ProviderUsage(StrictModel):
    model_id: str
    provider: Literal["mock", "openai", "zhipu", "deepseek", "openai_compatible"] = "mock"
    request_id: str | None = None
    raw_output_hash: str | None = Field(default=None, min_length=64, max_length=64)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cost_cny: Decimal = Field(default=Decimal("0"), ge=0)


class ProviderPlanResponse(StrictModel):
    candidate: PlanCandidate
    usage: ProviderUsage
    # Optional LLM violation-type classification emitted by the
    # business-repair prompt; None on every non-repair path.
    violation_category: Annotated[str | None, Field(max_length=64)] = None


class ProviderToolCall(StrictModel):
    call_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, object]


class AgentTurnResponse(StrictModel):
    final: PlanCandidate | None = None
    tool_calls: list[ProviderToolCall] = Field(default_factory=list, max_length=2)
    usage: ProviderUsage

    @model_validator(mode="after")
    def exactly_one_result(self) -> "AgentTurnResponse":
        if (self.final is None) == (not self.tool_calls):
            raise ValueError("AgentTurn must contain exactly one of final or tool_calls")
        return self


class MemoryParcel(StrictModel):
    """Output of the parallel memory_loader node (L2 Personal layer)."""

    selected: list[MemoryContext] = Field(default_factory=list)
    query_hash: str = ""
    pinned_count: int = 0
    semantic_count: int = 0
    fallback_used: bool = False
    retrieval_failed: bool = False
    retrieval_latency_ms: int = 0


class HistoryParcel(StrictModel):
    """Output of the parallel evidence_loader node (plans/reviews/history)."""

    source_plan: PlanContext | None = None
    source_review: ReviewContext | None = None
    source_plan_id: UUID | None = None
    source_plan_version: int | None = None
    planning_date: date | None = None
    recent_tasks: list[TaskContext] = Field(default_factory=list)
    recent_reviews: list[ReviewContext] = Field(default_factory=list)
    completed_facts: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    interview_training_actions: list[str] = Field(default_factory=list)


class PlanningState(TypedDict, total=False):
    run_id: UUID
    user_id: UUID
    request: RunRequestSnapshot
    runtime_config: RuntimeConfigSnapshot
    profile: ProfileContext | None
    server_replan_mode: ReplanMode | None
    risk: RiskResult
    intent: IntentResult
    planning_context: PlanningContext
    evidence_catalog: list[EvidenceCatalogItem]
    candidate_evidence_visibility: EvidenceVisibility
    tool_round: int
    tool_call_count: int
    candidate_plan: PlanCandidate
    validation_report: ValidationReport
    validation_attempt: int
    repair_count: int
    fallback_reason: str | None
    companion: CompanionMessageCandidate
    # Provenance of the finalized candidate for model-vs-engineering
    # attribution: "model_pass" | "format_repair" | "deterministic_repair"
    # | "llm_repair" | "fallback".
    plan_provenance: str
    # Rule codes that deterministic repair cannot handle; persisted for the
    # offline rule-iteration loop (unknown-rule backlog).
    unknown_rule_codes: list[str]
    # LLM-assigned violation type label from the business-repair prompt.
    violation_category: str
    # Parallel context fan-out parcels (memory_loader ∥ evidence_loader).
    memory_parcel: MemoryParcel
    history_parcel: HistoryParcel


class AgentEventPayload(StrictModel):
    run_id: UUID
    sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def preserve_sequence(self) -> "AgentEventPayload":
        return self
