import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCancelRun, useRun } from "@/api/agent-runs";
import { useCancelGoalBrief, useConfirmGoalBrief, useCreateGoalBrief, useRefineGoalBrief } from "@/api/goal-briefs";
import { useMe } from "@/api/auth";
import { useInterviews } from "@/api/interviews";
import { useJobTargets, useResumeVersions } from "@/api/resumes";
import { useRunEventStream } from "@/api/sse";
import type { ActivePlanResponse, AgentRunResponse, TaskResponse } from "@/api/types";
import { TodayPage } from "./TodayPage";

vi.mock("@/api/agent-runs", () => ({
  useCancelRun: vi.fn(),
  useRun: vi.fn(),
}));
vi.mock("@/api/goal-briefs", () => ({
  useCancelGoalBrief: vi.fn(),
  useConfirmGoalBrief: vi.fn(),
  useCreateGoalBrief: vi.fn(),
  useRefineGoalBrief: vi.fn(),
}));
vi.mock("@/api/auth", () => ({ useMe: vi.fn() }));
vi.mock("@/api/interviews", () => ({ useInterviews: vi.fn() }));
vi.mock("@/api/resumes", () => ({ useResumeVersions: vi.fn(), useJobTargets: vi.fn() }));
vi.mock("@/api/sse", () => ({ useRunEventStream: vi.fn() }));

function task(overrides: Partial<TaskResponse>): TaskResponse {
  return {
    task_id: "task-1",
    plan_id: "plan-new",
    title: "完成项目验收",
    task_type: "project",
    scheduled_date: "2026-08-10",
    order_index: 0,
    state: "completed",
    starter_action: "1. 运行测试；2. 保存结果；3. 记录结论",
    execution_steps: [
      { index: 0, text: "运行测试", completed: true },
      { index: 1, text: "保存结果", completed: true },
      { index: 2, text: "记录结论", completed: true },
    ],
    deliverable: "测试记录，包含命令和通过结果",
    deliverable_verified: true,
    verification_status: "passed",
    completion_ready: false,
    rationale: "先确认当前进度",
    estimated_minutes: 60,
    actual_minutes: 50,
    abandoned_reason: null,
    abandoned_reason_text: null,
    version: 2,
    started_at: "2026-08-10T01:00:00Z",
    completed_at: "2026-08-10T01:50:00Z",
    abandoned_at: null,
    created_at: "2026-08-10T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(2026, 7, 10, 12, 0, 0));
  vi.mocked(useCreateGoalBrief).mockReturnValue({ isPending: false, error: null, mutate: vi.fn() } as unknown as ReturnType<typeof useCreateGoalBrief>);
  vi.mocked(useRefineGoalBrief).mockReturnValue({ isPending: false, mutate: vi.fn() } as unknown as ReturnType<typeof useRefineGoalBrief>);
  vi.mocked(useConfirmGoalBrief).mockReturnValue({ isPending: false, mutate: vi.fn() } as unknown as ReturnType<typeof useConfirmGoalBrief>);
  vi.mocked(useCancelGoalBrief).mockReturnValue({ isPending: false, mutate: vi.fn() } as unknown as ReturnType<typeof useCancelGoalBrief>);
  vi.mocked(useInterviews).mockReturnValue({ data: { items: [] } } as unknown as ReturnType<typeof useInterviews>);
  vi.mocked(useResumeVersions).mockReturnValue({ data: { items: [] } } as unknown as ReturnType<typeof useResumeVersions>);
  vi.mocked(useJobTargets).mockReturnValue({ data: { items: [] } } as unknown as ReturnType<typeof useJobTargets>);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("TodayPage", () => {
  it("refreshes a completed replan but only renders today's tasks", async () => {
    const completed = task({});
    const next = task({
      task_id: "task-2",
      title: "整理简历项目表达",
      scheduled_date: "2026-08-11",
      state: "pending",
      actual_minutes: null,
      started_at: null,
      completed_at: null,
      starter_action: "1. 写背景；2. 补技术取舍；3. 压缩成三条要点",
      deliverable: "3条项目描述，每条包含动作和结果",
    });
    const activePlan = {
      plan_id: "plan-new",
      status: "active",
      plan_date: "2026-08-10",
      horizon_start: "2026-08-10",
      horizon_end: "2026-09-06",
      overall_direction: "完成可展示的 Agent 项目并准备面试",
      weekly_focus: [{ week_index: 1, focus: "完成项目", success_signal: "测试通过" }],
      summary: "根据当前进度继续推进",
      rationale: "结合已完成任务重新安排",
      adjustment_reason: "每日时间已更新",
      sources: [],
      tasks: [completed, next],
      companion_message: null,
      version: 2,
      adopted_at: "2026-08-10T01:00:00Z",
      created_at: "2026-08-10T00:00:00Z",
    } satisfies ActivePlanResponse;
    const refetch = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useMe).mockReturnValue({
      data: {
        profile_complete: true,
        profile: {
          goal_type: "agent_app",
          stage: "preparing",
          time_budget_minutes: 90,
          skill_level: "intermediate",
        },
        active_plan: activePlan,
        today_tasks: [completed],
        active_run: null,
      },
      isLoading: false,
      refetch,
    } as unknown as ReturnType<typeof useMe>);
    vi.mocked(useRun).mockReturnValue({
      data: {
        run_id: "run-2",
        status: "completed",
        result_kind: "plan",
        final_plan_id: "plan-new",
      } as AgentRunResponse,
    } as ReturnType<typeof useRun>);
    vi.mocked(useRunEventStream).mockReturnValue({} as ReturnType<typeof useRunEventStream>);
    vi.mocked(useCancelRun).mockReturnValue({ mutate: vi.fn() } as unknown as ReturnType<typeof useCancelRun>);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/today?run_id=run-2"]}><TodayPage /></MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("heading", { name: "今天的计划" })).toBeInTheDocument();
    expect(screen.getByText("已完成 1/1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /完成项目验收/ })).toHaveAttribute(
      "href",
      "/journey/plan-new/day/2026-08-10",
    );
    expect(screen.queryByText("整理简历项目表达")).not.toBeInTheDocument();
    expect(screen.queryByText("本周每日计划")).not.toBeInTheDocument();
    expect(screen.queryByText("这一步在路线中的位置")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /回到工作台/ })).toHaveAttribute(
      "href",
      "/workspace",
    );
    await waitFor(() => expect(refetch).toHaveBeenCalledTimes(1));
  });

  it("renders a typed navigation result as a concrete next action", () => {
    vi.mocked(useMe).mockReturnValue({
      data: {
        profile_complete: true,
        profile: {
          goal_type: "agent_app",
          stage: "preparing",
          time_budget_minutes: 90,
          skill_level: "intermediate",
        },
        active_plan: null,
        today_tasks: [],
        active_run: null,
      },
      isLoading: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMe>);
    vi.mocked(useRun).mockReturnValue({
      data: {
        run_id: "run-navigation",
        status: "degraded",
        user_status: "action_required",
        status_message: "可以直接前往对应页面继续。",
        result_kind: "navigation",
        result: {
          action: "view_today_tasks",
          label: "查看今日任务",
          target_route: "/today",
          message: "这个请求不需要重新生成计划，可以直接查看今天的任务。",
        },
        final_plan_id: null,
      } as AgentRunResponse,
    } as ReturnType<typeof useRun>);
    vi.mocked(useRunEventStream).mockReturnValue({} as ReturnType<typeof useRunEventStream>);
    vi.mocked(useCancelRun).mockReturnValue({ mutate: vi.fn() } as unknown as ReturnType<typeof useCancelRun>);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/today?run_id=run-navigation"]}><TodayPage /></MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText("这个请求不需要重新生成计划，可以直接查看今天的任务。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看今日任务" })).toHaveAttribute("href", "/today");
  });

  it("uses the persistent stopping state and disables duplicate cancellation", () => {
    vi.mocked(useMe).mockReturnValue({
      data: {
        profile_complete: true,
        profile: null,
        active_plan: null,
        today_tasks: [],
        active_run: null,
      },
      isLoading: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMe>);
    vi.mocked(useRun).mockReturnValue({
      data: {
        run_id: "run-stopping",
        status: "running",
        user_status: "stopping",
        status_message: "正在安全停止本次生成。",
        result_kind: null,
        result: null,
        final_plan_id: null,
      } as AgentRunResponse,
    } as ReturnType<typeof useRun>);
    vi.mocked(useRunEventStream).mockReturnValue({
      connectionState: "live",
      progressMessage: null,
    } as ReturnType<typeof useRunEventStream>);
    vi.mocked(useCancelRun).mockReturnValue({ mutate: vi.fn() } as unknown as ReturnType<typeof useCancelRun>);

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/today?run_id=run-stopping"]}><TodayPage /></MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByText("正在安全停止本次生成。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "正在停止…" })).toBeDisabled();
  });
});
