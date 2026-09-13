import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeveloperEvalsPage } from "./DeveloperEvalsPage";

function renderPage(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><DeveloperEvalsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const experiment = {
  experiment_id: "11111111-1111-1111-1111-111111111111",
  status: "completed",
  execution_mode: "mock_provider",
  dataset_id: "runtime-smoke-v1",
  dataset_version: "1",
  trial_count: 1,
  started_at: "2026-08-09T00:00:00Z",
  finished_at: "2026-08-09T00:00:01Z",
  cancel_requested_at: null,
  variant_role: "baseline",
  baseline_experiment_id: null,
  agent_variant: null,
  git_commit: "a".repeat(40),
  graph_version: "stage6b-v1",
  feature_stage: 6,
  prompt_version: "mock_plan_stage6_context_v1",
  model_version: "mock-v1",
  tool_version: "tool-registry-1.0",
  context_version: "planning-context-stage6-v1",
  memory_version: "three-layer-memory-stage6b-v1",
  search_version: "mock-search-v1",
  eval_harness_version: "eval-harness-v2",
};

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("DeveloperEvalsPage", () => {
  it("shows deterministic report and diagnostic calibration status", async () => {
    localStorage.setItem("cpb_access_token", "dev-token");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      let payload: unknown;
      let status = 200;
      if (url.includes("/pairwise/calibration/")) {
        payload = { error: { code: "EVAL_CALIBRATION_NOT_COMPUTED", message: "missing" } };
        status = 404;
      } else if (url.endsWith("/progress")) {
        payload = { ...experiment, completed_count: 1, running_count: 0, pending_count: 0, failed_count: 0, cancelled_count: 0, timed_out_count: 0, in_flight_trial_ids: [], estimated_progress: 1 };
      } else if (url.endsWith("/report")) {
        payload = { experiment_id: experiment.experiment_id, experiment_status: "completed", trial_count: 1, completed_trial_count: 1, scored_trial_count: 1, hard_gate_pass_fraction: 1, any_score_generated: true, trials: [{ tokens_in: 10, tokens_out: 20 }], case_stats: {}, experiment_stats: {}, failure_counts: { provider_transient_failure: 0 }, revision: 1, cancel_requested_at: null };
      } else if (url.endsWith(experiment.experiment_id)) {
        payload = { ...experiment, trials: [{ trial_id: "22222222-2222-2222-2222-222222222222", case_id: "runtime-tool-error-01", status: "completed", run_status: "degraded", result_kind: "plan", error_code: null }] };
      } else {
        payload = { items: [experiment], next_offset: null };
      }
      return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
    }));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /completed/ }));

    expect(await screen.findByText(/Stage 6/)).toBeInTheDocument();
    expect(screen.getByText(/diagnostic_only/)).toBeInTheDocument();
    expect(await screen.findByText(/1\/1 scored/)).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /live/i })).not.toBeInTheDocument();
  });

  it("creates only a small mock or fixture experiment from the browser", async () => {
    localStorage.setItem("cpb_access_token", "dev-token");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      let payload: unknown = { items: [], next_offset: null };
      if (init?.method === "POST") {
        payload = { experiment_id: experiment.experiment_id, status: "draft" };
      } else if (url.endsWith("/progress")) {
        payload = { ...experiment, status: "draft", estimated_progress: 0 };
      } else if (url.endsWith(experiment.experiment_id)) {
        payload = { ...experiment, status: "draft", trials: [] };
      }
      return new Response(JSON.stringify(payload), { status: init?.method === "POST" ? 202 : 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "创建确定性实验" }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some((call) => call[1]?.method === "POST")).toBe(true);
    });
    const postCall = fetchMock.mock.calls.find((call) => call[1]?.method === "POST");
    expect(postCall?.[0]).toBe("/api/v1/eval/runs");
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual(expect.objectContaining({
      provider_mode: "mock",
      trial_count: 1,
      cases: ["runtime-tool-error-01"],
    }));
  });
});
