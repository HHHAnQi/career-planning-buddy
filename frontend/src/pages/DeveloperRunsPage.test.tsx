import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeveloperRunsPage } from "./DeveloperRunsPage";

function renderPage(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><DeveloperRunsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("DeveloperRunsPage", () => {
  it("uses the normal authenticated session instead of a second token", async () => {
    localStorage.setItem("cpb_access_token", "dev-session-token");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    expect(await screen.findByText("运行记录")).toBeInTheDocument();
    expect(screen.queryByLabelText("Developer JWT")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/dev/runs",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer dev-session-token",
        }),
      }),
    );
  });

  it("renders a persisted trace and terminal invariant", async () => {
    localStorage.setItem("cpb_access_token", "dev-session-token");
    const run = {
      run_id: "11111111-1111-1111-1111-111111111111",
      replay_of_run_id: null,
      user_ref: "0123456789ab",
      status: "completed",
      result_kind: "plan",
      resolved_intent: "create_plan",
      graph_version: "stage5-v1",
      model_id: "mock-career-planner-v1",
      total_tokens_in: 100,
      total_tokens_out: 200,
      total_cost_cny: "0",
      total_latency_ms: 12,
      fallback_reason: null,
      error_code: null,
      created_at: "2026-08-01T00:00:00Z",
      finished_at: "2026-08-01T00:00:01Z",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.endsWith("/api/v1/dev/runs")
        ? { items: [run], next_cursor: null }
        : {
            run,
            request_text: "Create a plan",
            input_snapshot: { data: { profile: "redacted" }, sha256: "a".repeat(64) },
            config_snapshot: { data: { provider: "mock" }, sha256: "b".repeat(64) },
            result: { plan_id: "p" },
            steps: [{ sequence: 1, node_name: "risk_gate", status: "completed", latency_ms: 1, error_code: null }],
            tools: [],
            events: [{ sequence: 1, event_type: "run.completed", payload: {} }],
            terminal_invariant: { terminal_count: 1, terminal_is_last: true, valid: true },
          };
      return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
    }));

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /create_plan/ }));

    expect(await screen.findByText("通过")).toBeInTheDocument();
    expect(screen.getByText(/risk_gate/)).toBeInTheDocument();
    expect(screen.getByText(/run.completed/)).toBeInTheDocument();
  });
});
