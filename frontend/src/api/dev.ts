export interface DevRunSummary {
  run_id: string;
  replay_of_run_id: string | null;
  user_ref: string;
  status: string;
  result_kind: string | null;
  resolved_intent: string | null;
  graph_version: string;
  model_id: string | null;
  total_tokens_in: number;
  total_tokens_out: number;
  total_cost_cny: string;
  total_latency_ms: number;
  fallback_reason: string | null;
  error_code: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface DevRunListResponse {
  items: DevRunSummary[];
  next_cursor: string | null;
}

export interface DevRunDetail {
  run: DevRunSummary;
  request_text: string;
  input_snapshot: { data: unknown; sha256: string } | null;
  config_snapshot: { data: unknown; sha256: string };
  result: unknown;
  steps: Array<{
    sequence: number;
    node_name: string;
    status: string;
    latency_ms: number;
    error_code: string | null;
  }>;
  tools: Array<{
    tool_call_id: string;
    tool_name: string;
    success: boolean;
    provider: string | null;
    latency_ms: number;
    error_code: string | null;
  }>;
  events: Array<{ sequence: number; event_type: string; payload: unknown }>;
  terminal_invariant: {
    terminal_count: number;
    terminal_is_last: boolean;
    valid: boolean;
  };
}

export async function fetchDevRuns(): Promise<DevRunListResponse> {
  return apiRequest<DevRunListResponse>("/api/v1/dev/runs");
}

export async function fetchDevRun(runId: string): Promise<DevRunDetail> {
  return apiRequest<DevRunDetail>(`/api/v1/dev/runs/${runId}`);
}

export interface ReplayResponse {
  run_id: string;
  replay_of_run_id: string;
  status: string;
  deterministic: boolean;
  execution_kind: "replay_v2" | "legacy_trace_clone";
}

export async function replayDevRun(runId: string): Promise<ReplayResponse> {
  return apiRequest<ReplayResponse>(`/api/v1/dev/runs/${runId}/replay`, {
    method: "POST",
    body: { tool_mode: "fixture" },
  });
}
import { apiRequest } from "./client";
