"""Batch 1 interview workflow contracts."""

from datetime import datetime
from typing import Literal, TypedDict
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.base import StrictModel

InterviewType = Literal["role_focused", "resume_deep_dive"]
InterviewSessionStatus = Literal["draft", "active", "report_generating", "completed", "aborted"]
AnswerStatus = Literal["pending", "submitted", "skipped"]
AnalysisStatus = Literal["not_started", "running", "ready", "failed"]
QuestionType = Literal["technical", "project", "resume_claim", "followup"]
FindingVerdict = Literal["correct", "incorrect", "partially_correct", "insufficient_evidence"]
Severity = Literal["low", "medium", "high"]


class QuestionSource(StrictModel):
    kind: Literal["resume", "job_target", "answer"]
    ref: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=500)


class InterviewQuestionCandidate(StrictModel):
    topic_key: str = Field(min_length=1, max_length=120)
    question_type: QuestionType
    question_text: str = Field(min_length=5, max_length=1000)
    sources: list[QuestionSource] = Field(min_length=1, max_length=4)
    parent_turn_id: UUID | None = None


class FactualFinding(StrictModel):
    claim: str = Field(min_length=1, max_length=500)
    verdict: FindingVerdict
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=1000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def require_caution_for_high_error(self) -> "FactualFinding":
        if self.verdict == "incorrect" and self.severity == "high":
            if not self.evidence_refs and self.confidence > 0.6:
                raise ValueError("high-severity errors require evidence or low confidence")
        return self


class AnswerStructure(StrictModel):
    conclusion_first: bool
    logical_flow: Literal["clear", "mixed", "unclear"]
    specificity: Literal["specific", "mixed", "vague"]
    concision: Literal["concise", "balanced", "verbose"]


class TurnAnalysis(StrictModel):
    covered_key_points: list[str] = Field(default_factory=list, max_length=10)
    missing_key_points: list[str] = Field(default_factory=list, max_length=10)
    factual_findings: list[FactualFinding] = Field(default_factory=list, max_length=8)
    answer_structure: AnswerStructure
    improvement_actions: list[str] = Field(default_factory=list, max_length=5)
    suggested_outline: list[str] = Field(default_factory=list, max_length=6)
    followup_reason: str | None = Field(default=None, max_length=500)
    limitations: list[str] = Field(default_factory=list, max_length=5)


class AudioSegment(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class AudioAnalysis(StrictModel):
    transcript: str = Field(min_length=1, max_length=10_000)
    segments: list[AudioSegment] = Field(default_factory=list, max_length=500)
    duration_seconds: float | None = Field(default=None, gt=0)
    effective_words_per_minute: float | None = Field(default=None, ge=0)
    long_pause_count: int | None = Field(default=None, ge=0)
    preparation_seconds: float | None = Field(default=None, ge=0)
    filler_count: int
    repeated_phrase_count: int
    asr_confidence: float | None = Field(default=None, ge=0, le=1)
    timestamps_reliable: bool
    limitations: list[str] = Field(default_factory=list, max_length=5)


class InterviewAnswerCandidate(StrictModel):
    analysis: TurnAnalysis
    next_action: Literal["followup", "next", "finish"]
    next_question: InterviewQuestionCandidate | None = None

    @model_validator(mode="after")
    def question_matches_action(self) -> "InterviewAnswerCandidate":
        if (self.next_action == "finish") == (self.next_question is not None):
            raise ValueError("next question is required unless the interview finishes")
        return self


class InterviewWeakness(StrictModel):
    weakness_key: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=160)
    dimension: str = Field(min_length=1, max_length=80)
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    evidence_turn_ids: list[UUID] = Field(min_length=1, max_length=6)
    status: Literal["observed", "repeated", "improving"] = "observed"


class WeaknessComparison(StrictModel):
    weakness_key: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=160)
    dimension: str = Field(min_length=1, max_length=80)
    status: Literal[
        "improved", "unchanged", "regressed", "insufficient_comparable_evidence"
    ]
    baseline_severity: Severity
    current_severity: Severity | None = None
    baseline_evidence_turn_ids: list[UUID] = Field(min_length=1, max_length=6)
    current_evidence_turn_ids: list[UUID] = Field(default_factory=list, max_length=6)


class InterviewComparison(StrictModel):
    baseline_session_id: UUID
    current_session_id: UUID
    items: list[WeaknessComparison] = Field(min_length=1, max_length=3)


class TrainingAction(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    starter_action: str = Field(min_length=1, max_length=240)
    deliverable: str = Field(min_length=1, max_length=240)
    estimated_minutes: int = Field(ge=5, le=480)
    source_weakness_keys: list[str] = Field(min_length=1, max_length=3)


class InterviewReport(StrictModel):
    overall_summary: str = Field(min_length=1, max_length=1000)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    weaknesses: list[InterviewWeakness] = Field(min_length=1, max_length=3)
    dimension_summary: list[dict[str, object]] = Field(default_factory=list, max_length=6)
    recommended_training_actions: list[TrainingAction] = Field(min_length=1, max_length=3)
    comparison: InterviewComparison | None = None
    limitations: list[str] = Field(default_factory=list, max_length=5)


class InterviewCreateRequest(StrictModel):
    resume_version_id: UUID
    job_target_id: UUID
    interview_type: InterviewType
    question_limit: int = Field(default=4, ge=4, le=6)
    followup_limit: int = Field(default=2, ge=0, le=2)


class InterviewMemoryCandidateRequest(StrictModel):
    weakness_keys: list[str] = Field(default_factory=list, max_length=3)


class InterviewMemoryCandidateResponse(StrictModel):
    created_candidate_ids: list[UUID] = Field(default_factory=list, max_length=3)
    eligible_weakness_keys: list[str] = Field(default_factory=list, max_length=3)
    skipped_weakness_keys: list[str] = Field(default_factory=list, max_length=3)


class TrainingActionsRequest(StrictModel):
    action_indexes: list[int] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_action_indexes(self) -> "TrainingActionsRequest":
        if len(set(self.action_indexes)) != len(self.action_indexes) or any(
            index < 0 or index > 2 for index in self.action_indexes
        ):
            raise ValueError("action indexes must be unique values between 0 and 2")
        return self


class TrainingActionPreviewItem(StrictModel):
    action_index: int = Field(ge=0, le=2)
    action: TrainingAction
    task_id: UUID | None = None


class TrainingActionsPreviewResponse(StrictModel):
    interview_id: UUID
    mode: Literal["task_adjustment", "replan"]
    items: list[TrainingActionPreviewItem] = Field(min_length=1, max_length=3)
    confirmation_required: Literal[True] = True


class TrainingActionsConfirmResponse(StrictModel):
    interview_id: UUID
    mode: Literal["task_adjustment", "replan"]
    adjustment_ids: list[UUID] = Field(default_factory=list, max_length=3)
    run: "InterviewRunReference | None" = None


class InterviewRunReference(StrictModel):
    run_id: UUID
    status: Literal["pending"] = "pending"
    events_url: str


class InterviewRetestRequest(StrictModel):
    weakness_keys: list[str] = Field(min_length=1, max_length=3)
    resume_version_id: UUID | None = None
    job_target_id: UUID | None = None
    question_limit: int = Field(default=4, ge=4, le=6)
    followup_limit: int = Field(default=2, ge=0, le=2)


class InterviewAnswerRequest(StrictModel):
    answer_text: str = Field(min_length=1, max_length=10_000)
    turn_id: UUID
    version: int = Field(ge=1)


class InterviewVersionRequest(StrictModel):
    version: int = Field(ge=1)


class InterviewTurnResponse(StrictModel):
    turn_id: UUID
    ordinal: int
    parent_turn_id: UUID | None
    topic_key: str
    question_type: QuestionType
    question_text: str
    question_sources: list[QuestionSource]
    answer_text: str | None
    answer_status: AnswerStatus
    analysis_status: AnalysisStatus
    analysis: TurnAnalysis | None
    audio_analysis: AudioAnalysis | None = None
    version: int
    answered_at: datetime | None
    created_at: datetime


class InterviewActiveRun(StrictModel):
    run_id: UUID
    run_kind: Literal["interview_start", "interview_answer", "interview_report"]
    status: Literal["pending", "running"]
    events_url: str


class InterviewSessionResponse(StrictModel):
    interview_id: UUID
    resume_version_id: UUID
    job_target_id: UUID
    interview_type: InterviewType
    status: InterviewSessionStatus
    question_limit: int
    followup_limit: int
    asked_question_count: int
    followup_count: int
    current_turn_id: UUID | None
    active_run: InterviewActiveRun | None = None
    turns: list[InterviewTurnResponse]
    report_status: Literal["not_requested", "generating", "ready", "failed"]
    report: InterviewReport | None
    comparison_session_id: UUID | None
    retest_weakness_keys: list[str] = Field(default_factory=list, max_length=3)
    version: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InterviewListResponse(StrictModel):
    items: list[InterviewSessionResponse]


class InterviewRunResponse(StrictModel):
    interview_id: UUID
    run_id: UUID
    status: Literal["pending"] = "pending"
    events_url: str


class InterviewTurnResultSummary(StrictModel):
    interview_id: UUID
    turn_id: UUID
    session_status: InterviewSessionStatus
    next_turn_id: UUID | None


class InterviewReportResultSummary(StrictModel):
    interview_id: UUID
    report_version: int
    status: Literal["ready"] = "ready"


class InterviewContext(StrictModel):
    interview_id: UUID
    interview_type: InterviewType
    question_limit: int
    followup_limit: int
    asked_question_count: int
    followup_count: int
    resume_version_id: UUID
    resume_text: str
    resume_hash: str
    job_target_id: UUID
    job_title: str
    company: str | None
    jd_text: str
    jd_hash: str
    current_turn: InterviewTurnResponse | None = None
    recent_turns: list[InterviewTurnResponse] = Field(default_factory=list, max_length=2)
    earlier_turn_summary: list[dict[str, object]] = Field(default_factory=list, max_length=6)
    asked_fingerprints: list[str] = Field(default_factory=list, max_length=8)
    retest_weakness_keys: list[str] = Field(default_factory=list, max_length=3)
    baseline_weaknesses: list[InterviewWeakness] = Field(default_factory=list, max_length=3)


class InterviewState(TypedDict, total=False):
    run_id: UUID
    user_id: UUID
    run_kind: str
    interview_session_id: UUID
    interview_turn_id: UUID | None
    context: InterviewContext
    question: InterviewQuestionCandidate
    answer_result: InterviewAnswerCandidate
    report: InterviewReport
