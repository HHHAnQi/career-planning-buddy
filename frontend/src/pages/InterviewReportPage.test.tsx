import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InterviewReportPage } from "./InterviewReportPage";

afterEach(() => vi.restoreAllMocks());

describe("InterviewReportPage", () => {
  it("restores the evidence report from the resource API", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      interview_id: "session-1", resume_version_id: "resume-1", job_target_id: "target-1",
      interview_type: "role_focused", status: "completed", question_limit: 4, followup_limit: 2,
      asked_question_count: 1, followup_count: 0, current_turn_id: null, version: 4,
      started_at: null, completed_at: null, created_at: "2026-08-12T00:00:00Z", updated_at: "2026-08-12T00:00:00Z",
      report_status: "ready", comparison_session_id: null, retest_weakness_keys: [], turns: [{ turn_id: "turn-1", ordinal: 1, parent_turn_id: null, topic_key: "project", question_type: "project", question_text: "介绍项目", question_sources: [], answer_text: "我负责接口设计", answer_status: "submitted", analysis_status: "ready", analysis: { covered_key_points: [], missing_key_points: [], factual_findings: [], answer_structure: {}, improvement_actions: ["补充结果"], suggested_outline: [], followup_reason: null, limitations: [] }, version: 2, answered_at: null, created_at: "2026-08-12T00:00:00Z" }],
      report: { overall_summary: "只依据本场回答", strengths: [], weaknesses: [{ weakness_key: "evidence", topic: "证据密度", dimension: "communication", severity: "medium", confidence: 0.7, evidence_turn_ids: ["turn-1"], status: "observed" }], dimension_summary: [], recommended_training_actions: [{ title: "重写回答", starter_action: "先写结论", deliverable: "三段式回答", estimated_minutes: 20, source_weakness_keys: ["evidence"] }], comparison: null, limitations: ["不是长期能力结论"] }
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/interviews/session-1/report"]}><Routes><Route path="/interviews/:interviewId/report" element={<InterviewReportPage />} /></Routes></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByText("证据密度")).toBeInTheDocument();
    expect(screen.getByText(/第 1 题：介绍项目/)).toBeInTheDocument();
    expect(screen.getByText("重写回答")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("选择重写回答"));
    expect(screen.getByRole("button", { name: "预览加入训练计划" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "针对薄弱点开始复测" })).toBeInTheDocument();
  });
});
