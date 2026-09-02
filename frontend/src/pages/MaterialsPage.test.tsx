import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MaterialsPage } from "./MaterialsPage";

afterEach(() => vi.restoreAllMocks());

function renderPage(initialEntry = "/materials"): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}><MaterialsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("MaterialsPage", () => {
  it("extracts an uploaded resume into an editable preview before saving", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      requests.push({ url, init });
      if (url.endsWith("/resume-versions/extract")) {
        expect(init?.body).toBeInstanceOf(FormData);
        return new Response(JSON.stringify({
          filename: "backend-resume.txt",
          media_type: "text/plain",
          character_count: 27,
          source_text: "后端工程师\n负责 FastAPI 与 PostgreSQL 服务开发。",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/resume-versions") && init?.method === "POST") {
        return new Response(JSON.stringify({ resume_version_id: "resume-1" }), {
          status: 201, headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ items: [] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    });
    renderPage();

    fireEvent.change(screen.getByLabelText("上传简历文件"), {
      target: { files: [new File(["resume"], "backend-resume.txt", { type: "text/plain" })] },
    });

    expect(await screen.findByText(/已解析 backend-resume.txt/)).toBeInTheDocument();
    expect(screen.getByLabelText("简历文本")).toHaveValue("后端工程师\n负责 FastAPI 与 PostgreSQL 服务开发。");
    fireEvent.change(screen.getByLabelText("简历文本"), {
      target: { value: "后端工程师\n负责 FastAPI、PostgreSQL 服务开发与自动化测试。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存简历版本" }));

    await waitFor(() => expect(requests.some(({ url, init }) =>
      url.endsWith("/resume-versions") && init?.method === "POST"
    )).toBe(true));
    const save = requests.find(({ url, init }) => url.endsWith("/resume-versions") && init?.method === "POST");
    expect(JSON.parse(String(save?.init?.body))).toMatchObject({
      label: "backend-resume",
      source_type: "uploaded_file",
      source_filename: "backend-resume.txt",
      source_media_type: "text/plain",
      source_text: "后端工程师\n负责 FastAPI、PostgreSQL 服务开发与自动化测试。",
    });
  });

  it("loads the assessment bound to the Run and applies accepted rewrites only as one batch", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const claim = {
      claim_id: "claim_aaaaaaaaaaaaaaaa",
      claim_text: "负责异步队列。",
      verdict: "partially_supported",
      rationale: "证据只覆盖部分职责。",
      requirement_ids: ["req_aaaaaaaaaaaaaaaa"],
      evidence_turn_ids: [],
      consumed_tool_call_ids: ["00000000-0000-0000-0000-000000000005"],
      suggested_rewrite: "参与异步队列开发。",
      source_start: 0,
      source_end: 8,
      source_hash: "a".repeat(64),
    };
    const exactAssessment = {
      assessment_id: "assessment-exact",
      resume_version_id: "resume-1",
      job_target_id: "target-1",
      interview_session_id: null,
      claims: [claim],
      rewrite_decisions: [{
        assessment_id: "assessment-exact",
        claim_id: claim.claim_id,
        status: "accepted",
        original_suggestion: claim.suggested_rewrite,
        rewrite_text: "参与异步队列开发并补充可验证边界。",
        applied_resume_version_id: null,
        decided_at: "2026-08-14T00:00:00Z",
        applied_at: null,
      }],
      source_run_id: "run-1",
      context_manifest: null,
      limitations: ["诊断结果不自动覆盖原简历。"],
      created_at: "2026-08-14T00:00:00Z",
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      requests.push({ url, init });
      const json = (value: object, status = 200) => new Response(JSON.stringify(value), {
        status, headers: { "Content-Type": "application/json" },
      });
      if (url.endsWith("/agent-runs/run-1")) {
        return json({ run_id: "run-1", status: "completed", result_kind: "resume_optimization", result: { assessment_id: "assessment-exact", claim_count: 1 }, error_code: null });
      }
      if (url.endsWith("/resume-assessments/assessment-exact")) return json(exactAssessment);
      if (url.endsWith("/resume-assessments/assessment-exact/rewrites/apply-batch")) {
        return json({ decisions: exactAssessment.rewrite_decisions, resume_version: { resume_version_id: "resume-child" } });
      }
      if (url.endsWith("/resume-assessments")) {
        return json([{ ...exactAssessment, assessment_id: "assessment-old", claims: [{ ...claim, claim_text: "错误的列表首项" }] }]);
      }
      return json({ items: [] });
    });

    renderPage("/materials?run_id=run-1");

    expect(await screen.findByText("负责异步队列。")).toBeInTheDocument();
    expect(screen.queryByText("错误的列表首项")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览全部变更" }));
    expect(screen.getByText("最终版本变更清单")).toBeInTheDocument();
    expect(screen.getAllByText("参与异步队列开发并补充可验证边界。")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "确认并生成新版本" }));

    await waitFor(() => expect(requests.some(({ url, init }) =>
      url.endsWith("/resume-assessments/assessment-exact/rewrites/apply-batch")
      && init?.method === "POST"
    )).toBe(true));
    expect(requests.some(({ url }) => url.includes("/claims/") && url.endsWith("/apply"))).toBe(false);
  });
});
