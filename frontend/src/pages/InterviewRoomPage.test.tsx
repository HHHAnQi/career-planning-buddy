import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InterviewRoomPage } from "./InterviewRoomPage";

afterEach(() => vi.restoreAllMocks());

describe("InterviewRoomPage", () => {
  it("offers an in-place retry when first-question generation failed", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      interview_id: "session-1",
      resume_version_id: "resume-1",
      job_target_id: "target-1",
      interview_type: "role_focused",
      status: "draft",
      question_limit: 4,
      followup_limit: 2,
      asked_question_count: 0,
      followup_count: 0,
      current_turn_id: null,
      version: 2,
      started_at: null,
      completed_at: null,
      created_at: "2026-08-12T00:00:00Z",
      updated_at: "2026-08-12T00:00:00Z",
      report_status: "not_requested",
      turns: [],
      report: null,
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/interviews/session-1"]}>
          <Routes>
            <Route path="/interviews/:interviewId" element={<InterviewRoomPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("重试生成第一题")).toBeInTheDocument();
    expect(screen.getByText(/简历、JD 和面试设置均已保存/)).toBeInTheDocument();
    expect(screen.queryByText("正在恢复面试状态…")).not.toBeInTheDocument();
  });

  it("restores an active answer Run after a page refresh", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/agent-runs/run-answer")) {
        return new Response(JSON.stringify({
          run_id: "run-answer",
          run_kind: "interview_answer",
          status: "running",
          user_status: "generating",
          status_message: "正在分析回答",
          result_kind: null,
          result: null,
          final_plan_id: null,
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response(JSON.stringify({
        interview_id: "session-1",
        resume_version_id: "resume-1",
        job_target_id: "target-1",
        interview_type: "role_focused",
        status: "active",
        question_limit: 4,
        followup_limit: 2,
        asked_question_count: 1,
        followup_count: 0,
        current_turn_id: "turn-1",
        active_run: { run_id: "run-answer", run_kind: "interview_answer", status: "running", events_url: "/events" },
        version: 2,
        started_at: "2026-08-12T00:00:00Z",
        completed_at: null,
        created_at: "2026-08-12T00:00:00Z",
        updated_at: "2026-08-12T00:00:00Z",
        report_status: "not_requested",
        turns: [{
          turn_id: "turn-1", ordinal: 1, parent_turn_id: null, topic_key: "api", question_type: "technical",
          question_text: "如何设计一个可靠的接口？", question_sources: [], answer_text: "先定义契约。",
          answer_status: "submitted", analysis_status: "running", analysis: null, audio_analysis: null,
          version: 2, answered_at: "2026-08-12T00:01:00Z", created_at: "2026-08-12T00:00:00Z",
        }],
        report: null,
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/interviews/session-1"]}><Routes><Route path="/interviews/:interviewId" element={<InterviewRoomPage />} /></Routes></MemoryRouter></QueryClientProvider>);

    expect(await screen.findByText("AI 正在分析，页面刷新后也可恢复。")).toBeInTheDocument();
    expect(screen.queryByText("分析失败，但原回答已安全保存。")).not.toBeInTheDocument();
  });
});
