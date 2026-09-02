import { ApiError, apiRequest } from "./client";

export interface EvalRunListItem {
  experiment_id: string;
  status: string;
  execution_mode: string;
  dataset_id: string;
  dataset_version: string;
  trial_count: number;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested_at: string | null;
  variant_role: "baseline" | "candidate";
  baseline_experiment_id: string | null;
  agent_variant: string | null;
  git_commit: string;
  graph_version: string;
  feature_stage: number;
  prompt_version: string;
  model_version: string;
  tool_version: string;
  context_version: string;
  memory_version: string;
  search_version: string;
  eval_harness_version: string;
}

export interface EvalRunListResponse {
  items: EvalRunListItem[];
  next_offset: number | null;
}

export interface EvalTrialStatus {
  trial_id: string;
  case_id: string;
  status: string;
  run_status: string | null;
  result_kind: string | null;
  error_code: string | null;
}

export interface EvalRunStatus extends EvalRunListItem {
  trials: EvalTrialStatus[];
}

export interface EvalRunProgress {
  experiment_id: string;
  status: string;
  trial_count: number;
  completed_count: number;
  running_count: number;
  pending_count: number;
  failed_count: number;
  cancelled_count: number;
  timed_out_count: number;
  in_flight_trial_ids: string[];
  cancel_requested_at: string | null;
  estimated_progress: number;
}

export interface EvalRunReport {
  experiment_id: string;
  experiment_status: string;
  trial_count: number;
  completed_trial_count: number;
  scored_trial_count: number;
  hard_gate_pass_fraction: number;
  any_score_generated: boolean;
  trials: Array<Record<string, unknown>>;
  case_stats: Record<string, Record<string, unknown>>;
  experiment_stats: Record<string, unknown> | null;
  failure_counts: Record<string, number>;
  revision: number;
  cancel_requested_at: string | null;
}

export interface CalibrationStatus {
  calibration_status: "passing" | "failing" | "insufficient";
  usage_mode: "diagnostic_only" | "gate_eligible";
  created_at: string;
}

export function fetchEvalRuns(): Promise<EvalRunListResponse> {
  return apiRequest<EvalRunListResponse>("/api/v1/eval/runs?limit=50&offset=0");
}

export function createEvalRun(input: {
  dataset: "stage5" | "runtime-smoke";
  providerMode: "mock" | "fixture";
}): Promise<{ experiment_id: string; status: string }> {
  return apiRequest("/api/v1/eval/runs", {
    method: "POST",
    body: {
      dataset: input.dataset,
      cases: input.dataset === "runtime-smoke" ? ["runtime-tool-error-01"] : null,
      provider_mode: input.providerMode,
      trial_count: 1,
      grade: true,
    },
  });
}

export function fetchEvalStatus(experimentId: string): Promise<EvalRunStatus> {
  return apiRequest<EvalRunStatus>(`/api/v1/eval/runs/${experimentId}`);
}

export function fetchEvalProgress(experimentId: string): Promise<EvalRunProgress> {
  return apiRequest<EvalRunProgress>(`/api/v1/eval/runs/${experimentId}/progress`);
}

export function fetchEvalReport(experimentId: string): Promise<EvalRunReport> {
  return apiRequest<EvalRunReport>(`/api/v1/eval/runs/${experimentId}/report`);
}

export function cancelEvalRun(experimentId: string): Promise<void> {
  return apiRequest(`/api/v1/eval/runs/${experimentId}/cancel`, { method: "POST" });
}

export async function fetchLatestCalibration(
  datasetId: string,
  datasetVersion: string,
): Promise<CalibrationStatus | null> {
  try {
    return await apiRequest<CalibrationStatus>(
      `/api/v1/eval/pairwise/calibration/${encodeURIComponent(datasetId)}/${encodeURIComponent(datasetVersion)}/latest`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}
