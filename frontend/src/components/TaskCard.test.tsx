import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskResponse } from "@/api/types";
import { TaskCard } from "./TaskCard";

const task: TaskResponse = {
  task_id: "task-1",
  plan_id: "plan-1",
  title: "整理项目经历",
  task_type: "resume",
  scheduled_date: "2026-08-04",
  order_index: 0,
  state: "pending",
  starter_action: "打开简历",
  execution_steps: [{ index: 0, text: "打开简历", completed: false }],
  deliverable: "一段 STAR 描述",
  deliverable_verified: false,
  verification_status: "not_ready",
  completion_ready: false,
  rationale: null,
  estimated_minutes: 30,
  actual_minutes: null,
  abandoned_reason: null,
  abandoned_reason_text: null,
  version: 1,
  started_at: null,
  completed_at: null,
  abandoned_at: null,
  created_at: "2026-08-04T00:00:00Z",
};

beforeEach(() => {
  localStorage.setItem("cpb_access_token", "guest-token");
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("TaskCard", () => {
  it("updates a pending task to in_progress with its optimistic-lock version", async () => {
    const feedback = vi.fn();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      new Response(
        JSON.stringify({
          task: { ...task, state: "in_progress", version: 2 },
          plan_status: "active",
          companion_message: "已经开始，先完成第一步。",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <TaskCard task={task} onFeedback={feedback} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("link", { name: /整理项目经历/ })).toHaveAttribute(
      "href",
      "/journey/plan-1/day/2026-08-04",
    );
    fireEvent.click(screen.getByRole("button", { name: "开始：整理项目经历" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const call = fetchMock.mock.calls[0];
    expect(call).toBeDefined();
    if (call === undefined) throw new Error("expected one task update request");
    const [url, init] = call;
    expect(String(url)).toContain("/api/v1/tasks/task-1");
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ state: "in_progress", version: 1 });
    await waitFor(() => expect(feedback).toHaveBeenCalledWith("已经开始，先完成第一步。"));
  });

  it("starts verification only after every execution step is complete", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <TaskCard task={{
            ...task,
            state: "in_progress",
            version: 2,
            execution_steps: [{ index: 0, text: "打开简历", completed: true }],
            deliverable_verified: false,
            verification_status: "ready",
            completion_ready: true,
          }} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "验收：整理项目经历" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "还未达到" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已达到，继续" })).toBeInTheDocument();
  });

  it("keeps a completed task visible and lets the user mark it incomplete", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          task: {
            ...task,
            state: "in_progress",
            version: 4,
            actual_minutes: null,
            completed_at: null,
          },
          plan_status: "active",
          companion_message: "已恢复为进行中。",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <TaskCard
            task={{
              ...task,
              state: "completed",
              starter_action: "1. 选择文档类型与向量数据库 2. 确定 LLM 与检索策略",
              execution_steps: [
                { index: 0, text: "选择文档类型与向量数据库", completed: true },
                { index: 1, text: "确定 LLM 与检索策略", completed: true },
              ],
              deliverable_verified: true,
              verification_status: "passed",
              completion_ready: false,
              version: 3,
              actual_minutes: 25,
              completed_at: "2026-08-04T01:00:00Z",
            }}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("button", { name: "已完成：整理项目经历" })).toBeDisabled();
    expect(screen.getByText("选择文档类型与向量数据库")).toBeInTheDocument();
    expect(screen.getByText("确定 LLM 与检索策略")).toBeInTheDocument();
    expect(screen.getByText("验收标准")).toBeInTheDocument();
    expect(screen.getByText("已通过")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "撤销完成" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ state: "in_progress", version: 3 });
  });

  it("persists a reversible execution-step check", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          task: {
            ...task,
            state: "in_progress",
            version: 2,
            execution_steps: [{ index: 0, text: "打开简历", completed: true }],
          },
          plan_status: "active",
          companion_message: "执行进度已更新。",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><TaskCard task={task} /></MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "完成步骤：打开简历" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/api/v1/tasks/task-1/checklist");
    expect(JSON.parse(String(init?.body))).toEqual({
      version: 1,
      step_index: 0,
      step_completed: true,
    });
  });

  it("records failed verification without completing the task", async () => {
    const readyTask: TaskResponse = {
      ...task,
      state: "in_progress",
      version: 2,
      execution_steps: [{ index: 0, text: "打开简历", completed: true }],
      verification_status: "ready",
      completion_ready: true,
    };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(
        JSON.stringify({
          task: { ...readyTask, version: 3, verification_status: "failed" },
          plan_status: "active",
          companion_message: "已记录验收未通过。",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter><TaskCard task={readyTask} /></MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /开始验收/ }));
    fireEvent.click(screen.getByRole("button", { name: "还未达到" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(url)).toContain("/api/v1/tasks/task-1/verification");
    expect(JSON.parse(String(init?.body))).toEqual({ passed: false, version: 2 });
  });
});
