/**
 * 后端契约类型，从 OpenAPI 精确对齐。
 */

export type GoalType =
  | "ai_backend"
  | "agent_app"
  | "backend_java"
  | "data_engineer"
  | "fullstack"
  | "other";

export type CareerStage = "exploring" | "preparing" | "applying" | "interviewing";
export type SkillLevel = "beginner" | "intermediate" | "advanced";

export type TaskType =
  | "learning"
  | "project"
  | "interview"
  | "application"
  | "resume"
  | "other";

export type TaskStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "abandoned"
  | "expired";

export type AbandonedReason =
  | "too_hard"
  | "too_easy"
  | "no_time"
  | "lost_interest"
  | "blocked"
  | "other";

export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "degraded"
  | "failed"
  | "cancelled";

export type RunResultKind = "plan" | "clarification" | "safe_response" | "navigation" | "interview_turn" | "interview_report" | "resume_assessment" | "resume_optimization";
export type RunIntent = "create_plan" | "replan" | "navigate" | "unsupported" | "interview_start" | "interview_answer" | "interview_report" | "resume_assessment" | "resume_optimization";
export type RunKind = "planning" | "interview_start" | "interview_answer" | "interview_report" | "resume_assessment" | "resume_optimization";
export type RunUserStatus =
  | "queued"
  | "generating"
  | "recovering"
  | "stopping"
  | "ready"
  | "action_required"
  | "failed"
  | "cancelled";
export type ReplanMode = "initial" | "continue" | "adjust";
export type NextPlanAction = "continue" | "adjust";
export type PlanStatus = "generated" | "active" | "completed" | "archived";

export interface ProfilePreferences {
  target_companies: string[];
  preferred_time_slot: string | null;
  weekly_available_days: number[];
}

export type ProfilePreferencesInput = Partial<ProfilePreferences>;

export interface ProfileResponse {
  goal_type: GoalType;
  stage: CareerStage;
  time_budget_minutes: number;
  skill_level: SkillLevel;
  skill_summary: string | null;
  start_date: string | null;
  deadline: string | null;
  preferences: ProfilePreferences;
  version: number;
}

export interface ProfilePutRequest {
  goal_type: GoalType;
  stage: CareerStage;
  time_budget_minutes: number;
  skill_level: SkillLevel;
  skill_summary?: string | null;
  start_date: string;
  deadline: string;
  preferences?: ProfilePreferencesInput;
}

export interface ProfilePatchRequest {
  version: number;
  goal_type?: GoalType;
  stage?: CareerStage;
  time_budget_minutes?: number;
  skill_level?: SkillLevel;
  skill_summary?: string | null;
  start_date?: string | null;
  deadline?: string | null;
  preferences?: ProfilePreferencesInput;
}

export interface WeeklyFocus {
  week_index: number;
  focus: string;
  success_signal: string;
}

export interface TaskResponse {
  task_id: string;
  plan_id: string;
  title: string;
  task_type: TaskType;
  scheduled_date: string;
  order_index: number;
  state: TaskStatus;
  starter_action: string;
  execution_steps: TaskExecutionStep[];
  deliverable: string;
  deliverable_verified: boolean;
  verification_status: "not_ready" | "ready" | "failed" | "passed";
  completion_ready: boolean;
  rationale: string | null;
  estimated_minutes: number;
  actual_minutes: number | null;
  abandoned_reason: AbandonedReason | null;
  abandoned_reason_text: string | null;
  version: number;
  started_at: string | null;
  completed_at: string | null;
  abandoned_at: string | null;
  created_at: string;
}

export interface TaskExecutionStep {
  index: number;
  text: string;
  completed: boolean;
}

export interface TaskDetailResponse {
  task: TaskResponse;
  week_focus: string;
  week_success_signal: string;
  editable: boolean;
  edit_reason: string | null;
}

export interface TaskEditFields {
  title?: string;
  starter_action?: string;
  deliverable?: string;
  rationale?: string;
  estimated_minutes?: number;
}

export interface TaskEditRequest extends TaskEditFields {
  version: number;
}

export interface TaskEditResponse {
  task: TaskResponse;
  adjustment_id: string;
  companion_message: string;
}

export interface TaskAdjustmentProposalResponse {
  adjustment_id: string;
  plan_id: string;
  task_id: string;
  status: "pending" | "applied" | "rejected";
  request_text: string;
  original_task: Record<string, unknown>;
  proposed_patch: TaskEditFields;
  rationale: string;
  generation_method: "manual" | "rule" | "model" | "rule_fallback";
  model_id: string | null;
  task_version: number;
  version: number;
  created_at: string;
}

export interface PlanSourceResponse {
  kind: "memory" | "experience_atom" | "search_source";
  id: string;
  available: boolean;
  title: string | null;
  url?: string | null;
  snippet?: string | null;
  reliability?: number | null;
}

export interface ActivePlanResponse {
  plan_id: string;
  status: PlanStatus;
  plan_date: string;
  horizon_start: string;
  horizon_end: string;
  overall_direction: string;
  weekly_focus: WeeklyFocus[];
  summary: string;
  rationale: string;
  adjustment_reason: string | null;
  sources: PlanSourceResponse[];
  tasks: TaskResponse[];
  companion_message: string | null;
  version: number;
  adopted_at: string | null;
  created_at: string;
}

export interface PlanResultSummary {
  plan_id: string;
  status: PlanStatus;
  plan_date: string;
  horizon_end: string;
  summary: string;
  task_count: number;
}

export interface ClarificationRequestPayload {
  questions: string[];
  slot_names: string[];
  hint_options: Record<string, string[]>;
  reason: string;
  message: string;
  suggested_actions: SuggestedAction[];
  target_route: "/settings/profile" | "/journey" | "/today" | null;
}

export interface SuggestedAction {
  action: "complete_profile" | "create_plan" | "continue_plan" | "adjust_plan" | "view_current_plan" | "view_today_tasks";
  label: string;
  target_route: "/settings/profile" | "/journey" | "/today" | "/reviews";
}

export interface NavigationResultPayload {
  action: "view_current_plan" | "view_today_tasks";
  label: string;
  target_route: "/journey" | "/today";
  message: string;
}

export interface SafeResponsePayload {
  message: string;
  resource_ids: string[];
  disclaimer: string;
}

export interface ResumeVersionResponse {
  resume_version_id: string;
  label: string;
  source_type: "pasted_text" | "uploaded_file";
  source_text: string;
  structured: Record<string, unknown>;
  content_hash: string;
  parent_version_id: string | null;
  created_at: string;
}

export interface ResumeDocumentExtractResponse {
  filename: string;
  media_type: "application/pdf" | "application/vnd.openxmlformats-officedocument.wordprocessingml.document" | "text/plain";
  character_count: number;
  source_text: string;
}

export interface JobTargetResponse {
  job_target_id: string;
  title: string;
  company: string | null;
  jd_text: string;
  requirements: Record<string, unknown>;
  content_hash: string;
  created_at: string;
}

export interface QuestionSource {
  kind: "resume" | "job_target" | "answer";
  ref: string;
  excerpt: string;
}

export interface TurnAnalysis {
  covered_key_points: string[];
  missing_key_points: string[];
  factual_findings: Array<{
    claim: string;
    verdict: "correct" | "incorrect" | "partially_correct" | "insufficient_evidence";
    severity: "low" | "medium" | "high";
    confidence: number;
    rationale: string;
    evidence_refs: string[];
  }>;
  answer_structure: Record<string, unknown>;
  improvement_actions: string[];
  suggested_outline: string[];
  followup_reason: string | null;
  limitations: string[];
}

export interface AudioAnalysis {
  transcript: string;
  segments: Array<{ text: string; start_seconds: number; end_seconds: number }>;
  duration_seconds: number | null;
  effective_words_per_minute: number | null;
  long_pause_count: number | null;
  preparation_seconds: number | null;
  filler_count: number;
  repeated_phrase_count: number;
  asr_confidence: number | null;
  timestamps_reliable: boolean;
  limitations: string[];
}

export interface InterviewTurnResponse {
  turn_id: string;
  ordinal: number;
  parent_turn_id: string | null;
  topic_key: string;
  question_type: "technical" | "project" | "resume_claim" | "followup";
  question_text: string;
  question_sources: QuestionSource[];
  answer_text: string | null;
  answer_status: "pending" | "submitted" | "skipped";
  analysis_status: "not_started" | "running" | "ready" | "failed";
  analysis: TurnAnalysis | null;
  audio_analysis: AudioAnalysis | null;
  version: number;
  answered_at: string | null;
  created_at: string;
}

export interface InterviewWeakness {
  weakness_key: string;
  topic: string;
  dimension: string;
  severity: "low" | "medium" | "high";
  confidence: number;
  evidence_turn_ids: string[];
  status: "observed" | "repeated" | "improving";
}

export interface WeaknessComparison {
  weakness_key: string;
  topic: string;
  dimension: string;
  status: "improved" | "unchanged" | "regressed" | "insufficient_comparable_evidence";
  baseline_severity: "low" | "medium" | "high";
  current_severity: "low" | "medium" | "high" | null;
  baseline_evidence_turn_ids: string[];
  current_evidence_turn_ids: string[];
}

export interface InterviewComparison {
  baseline_session_id: string;
  current_session_id: string;
  items: WeaknessComparison[];
}

export interface InterviewReport {
  overall_summary: string;
  strengths: string[];
  weaknesses: InterviewWeakness[];
  dimension_summary: Array<Record<string, unknown>>;
  recommended_training_actions: Array<{
    title: string;
    starter_action: string;
    deliverable: string;
    estimated_minutes: number;
    source_weakness_keys: string[];
  }>;
  comparison: InterviewComparison | null;
  limitations: string[];
}

export interface InterviewSessionResponse {
  interview_id: string;
  resume_version_id: string;
  job_target_id: string;
  interview_type: "role_focused" | "resume_deep_dive";
  status: "draft" | "active" | "report_generating" | "completed" | "aborted";
  question_limit: number;
  followup_limit: number;
  asked_question_count: number;
  followup_count: number;
  current_turn_id: string | null;
  active_run: {
    run_id: string;
    run_kind: "interview_start" | "interview_answer" | "interview_report";
    status: "pending" | "running";
    events_url: string;
  } | null;
  turns: InterviewTurnResponse[];
  report_status: "not_requested" | "generating" | "ready" | "failed";
  report: InterviewReport | null;
  comparison_session_id: string | null;
  retest_weakness_keys: string[];
  version: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InterviewRunResponse {
  interview_id: string;
  run_id: string;
  status: "pending";
  events_url: string;
}

export interface ResumeClaimFinding {
  claim_id: string;
  claim_text: string;
  verdict: "supported" | "partially_supported" | "unsupported" | "insufficient_evidence";
  rationale: string;
  requirement_ids: string[];
  evidence_turn_ids: string[];
  suggested_rewrite: string | null;
  consumed_tool_call_ids: string[];
  source_start: number | null;
  source_end: number | null;
  source_hash: string | null;
}

export interface ResumeRewriteDecisionResponse {
  assessment_id: string;
  claim_id: string;
  status: "accepted" | "rejected" | "applied";
  original_suggestion: string;
  rewrite_text: string | null;
  applied_resume_version_id: string | null;
  decided_at: string;
  applied_at: string | null;
}

export interface ResumeAssessmentResponse {
  assessment_id: string;
  resume_version_id: string;
  job_target_id: string;
  interview_session_id: string | null;
  claims: ResumeClaimFinding[];
  rewrite_decisions: ResumeRewriteDecisionResponse[];
  source_run_id: string | null;
  context_manifest: {
    algorithm_version: string;
    token_budget: number;
    used_tokens: number;
    actual_prompt_tokens: number | null;
    rendered_context_hash: string | null;
    embedding_provider: string | null;
    selected_evidence_refs: string[];
    prompt_injection_filtered_count: number;
    candidates: Array<{ selected: boolean; source_type: string; source_id: string; evidence_ref: string; rendered_content: string | null; selection_reason: string | null; exclusion_reason: string | null }>;
  } | null;
  limitations: string[];
  created_at: string;
}

export interface ResumeOptimizationRunResponse {
  run_id: string;
  status: "pending";
  events_url: string;
}

export interface ResumeRewriteBatchApplyResponse {
  decisions: ResumeRewriteDecisionResponse[];
  resume_version: ResumeVersionResponse;
}

export interface ResumeRewriteApplyResponse {
  decision: ResumeRewriteDecisionResponse;
  resume_version: ResumeVersionResponse;
}

export interface TrainingActionsPreviewResponse {
  interview_id: string;
  mode: "task_adjustment" | "replan";
  items: Array<{
    action_index: number;
    action: InterviewReport["recommended_training_actions"][number];
    task_id: string | null;
  }>;
  confirmation_required: true;
}

export interface TrainingActionsConfirmResponse {
  interview_id: string;
  mode: "task_adjustment" | "replan";
  adjustment_ids: string[];
  run: { run_id: string; status: "pending"; events_url: string } | null;
}

export interface InterviewTurnResultSummary {
  interview_id: string;
  turn_id: string;
  session_status: string;
  next_turn_id: string | null;
}

export interface InterviewReportResultSummary {
  interview_id: string;
  report_version: number;
  status: "ready";
}

export interface ResumeAssessmentResultSummary {
  assessment_id: string;
  claim_count: number;
}

export interface AgentRunResponse {
  run_id: string;
  run_kind: RunKind;
  status: RunStatus;
  user_status: RunUserStatus;
  status_message: string;
  resolved_intent: RunIntent | null;
  replan_mode: ReplanMode | null;
  result_kind: RunResultKind | null;
  result: PlanResultSummary | ClarificationRequestPayload | SafeResponsePayload | NavigationResultPayload | InterviewTurnResultSummary | InterviewReportResultSummary | ResumeAssessmentResultSummary | null;
  final_plan_id: string | null;
  fallback_reason: string | null;
  error_code: string | null;
  risk_category: string | null;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost_cny: string;
  total_latency_ms: number;
  created_at: string;
  finished_at: string | null;
  cancel_requested_at: string | null;
}

export interface AgentRunCreatedResponse {
  run_id: string;
  status: RunStatus;
  events_url: string;
}

export type GoalBriefStatus = "clarification_required" | "awaiting_confirmation" | "confirmed" | "cancelled";
export type ObjectiveType = "career_plan" | "project" | "application" | "interview" | "skill_transition";

export interface GoalBriefResponse {
  goal_brief_id: string;
  status: GoalBriefStatus;
  source_message: string;
  hint_intent: "create_plan" | "replan";
  source_plan_id: string | null;
  objective_type: ObjectiveType | null;
  target_role: string | null;
  objective: string | null;
  capability_focus: string[];
  tech_stack: string[];
  duration_weeks: number | null;
  deliverables: string[];
  success_criteria: string[];
  assumptions: string[];
  missing_fields: string[];
  questions: string[];
  extraction_method: "rule" | "model" | "rule_fallback";
  model_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface GoalBriefCreateRequest {
  message: string;
  hint_intent: "create_plan" | "replan";
  source_plan_id?: string | null;
}

export interface GoalBriefConfirmResponse {
  goal_brief: GoalBriefResponse;
  run: AgentRunCreatedResponse;
}

export interface UserSummary {
  id: string;
  email: string | null;
  display_name: string | null;
  role: "user" | "dev";
}

export interface MeResponse {
  user: UserSummary;
  profile_complete: boolean;
  planning_window_valid: boolean;
  profile: ProfileResponse | null;
  active_plan: ActivePlanResponse | null;
  today_tasks: TaskResponse[];
  latest_review: ReviewResponse | null;
  active_run: AgentRunResponse | null;
  active_goal_brief: GoalBriefResponse | null;
}

export interface GuestLoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: UserSummary;
}

export interface ReviewResponse {
  review_id: string;
  plan_id: string;
  review_date: string;
  mood: number;
  blockers: string | null;
  adjustment_request: string | null;
  free_text: string | null;
  completed_count: number;
  abandoned_count: number;
  suggested_replan: boolean;
  replan_reason: string | null;
  next_plan_action: NextPlanAction;
  companion_message: string;
  next_plan_run_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ReviewCreateRequest {
  plan_id: string;
  review_date: string;
  mood: number;
  blockers?: string | null;
  adjustment_request?: string | null;
  free_text?: string | null;
}

export interface ReviewUpdateRequest {
  version: number;
  mood?: number;
  blockers?: string | null;
  adjustment_request?: string | null;
  free_text?: string | null;
}

export interface StartNextPlanResponse {
  run_id: string;
  status: RunStatus;
  replan_mode: ReplanMode;
  events_url: string;
}

export interface TaskUpdateRequest {
  state: "in_progress" | "completed" | "abandoned";
  version: number;
  actual_minutes?: number | null;
  abandoned_reason?: AbandonedReason | null;
  abandoned_reason_text?: string | null;
}

export interface TaskUpdateResponse {
  task: TaskResponse;
  plan_status: PlanStatus;
  companion_message: string;
}

export interface TaskChecklistUpdateRequest {
  version: number;
  step_index: number;
  step_completed: boolean;
}

export interface TaskVerificationRequest {
  version: number;
  passed: boolean;
  actual_minutes?: number;
}

export interface MemoryResponse {
  memory_id: string;
  memory_type: "profile_fact" | "stable_preference" | "execution_pattern";
  summary: string;
  content: Record<string, unknown>;
  sensitivity: "normal" | "sensitive";
  status: "active" | "closed";
  source_run_id: string | null;
  version: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryCandidateResponse {
  candidate_id: string;
  memory_type: "profile_fact" | "stable_preference" | "execution_pattern";
  summary: string;
  content: Record<string, unknown>;
  sensitivity: "sensitive" | "highly_sensitive";
  status: "pending" | "confirmed" | "rejected" | "expired";
  proposed_by_run_id: string | null;
  activated_memory_id: string | null;
  expires_at: string;
  created_at: string;
  decided_at: string | null;
}

export interface MemoryCandidateDecisionResponse {
  candidate: MemoryCandidateResponse;
  memory: MemoryResponse | null;
}
