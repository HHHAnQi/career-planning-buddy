import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { useMe } from "@/api/auth";
import { useActivePlan } from "@/api/plans";
import { useCreateReview, useDeleteReview, useReviews, useStartNextPlan, useUpdateReview } from "@/api/reviews";
import { ReviewsPage } from "./ReviewsPage";

vi.mock("@/api/auth", () => ({ useMe: vi.fn() }));
vi.mock("@/api/plans", () => ({ useActivePlan: vi.fn() }));
vi.mock("@/api/reviews", () => ({
  useCreateReview: vi.fn(),
  useDeleteReview: vi.fn(),
  useReviews: vi.fn(),
  useStartNextPlan: vi.fn(),
  useUpdateReview: vi.fn(),
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(2026, 7, 11, 12, 0, 0));
  vi.mocked(useCreateReview).mockReturnValue({ isPending: false, error: null, mutate: vi.fn() } as unknown as ReturnType<typeof useCreateReview>);
  vi.mocked(useDeleteReview).mockReturnValue({ isPending: false, error: null, mutate: vi.fn() } as unknown as ReturnType<typeof useDeleteReview>);
  vi.mocked(useUpdateReview).mockReturnValue({ isPending: false, error: null, mutate: vi.fn() } as unknown as ReturnType<typeof useUpdateReview>);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("ReviewsPage", () => {
  it("shows the no-plan state without an unusable create button", () => {
    vi.mocked(useMe).mockReturnValue({
      data: { profile_complete: true, active_plan: null },
    } as unknown as ReturnType<typeof useMe>);
    vi.mocked(useActivePlan).mockReturnValue({
      data: undefined,
      isLoading: false,
    } as unknown as ReturnType<typeof useActivePlan>);
    vi.mocked(useReviews).mockReturnValue({
      data: { items: [] },
      isLoading: false,
    } as unknown as ReturnType<typeof useReviews>);
    vi.mocked(useStartNextPlan).mockReturnValue({
      isPending: false,
      mutate: vi.fn(),
    } as unknown as ReturnType<typeof useStartNextPlan>);

    render(
      <MemoryRouter>
        <ReviewsPage />
      </MemoryRouter>,
    );

    expect(
      screen.getByText("需要先生成至少一份计划才能开始复盘。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "新建复盘" }),
    ).not.toBeInTheDocument();
  });

  it("does not allow a daily review to replace an open fixed week", () => {
    const plan = fixedWeekPlan("pending");
    mockReviewPage(plan);

    render(<MemoryRouter><ReviewsPage /></MemoryRouter>);

    expect(screen.queryByRole("button", { name: /生成下一周/ })).not.toBeInTheDocument();
    expect(screen.getByText(/当前固定周周期仍在进行/)).toBeInTheDocument();
  });

  it("allows next-week generation after every task is settled", () => {
    const plan = fixedWeekPlan("completed");
    mockReviewPage(plan);

    render(<MemoryRouter><ReviewsPage /></MemoryRouter>);

    expect(screen.getByRole("button", { name: "完成本周结算并生成下一周 →" })).toBeInTheDocument();
  });

  it("lets the user open edit and delete interactions for an unconsumed review", () => {
    const plan = fixedWeekPlan("pending");
    mockReviewPage(plan);
    vi.mocked(useUpdateReview).mockReturnValue({
      isPending: false,
      error: null,
      mutate: vi.fn((_input, options) => options?.onSuccess?.({} as never, {} as never, undefined)),
    } as unknown as ReturnType<typeof useUpdateReview>);

    render(<MemoryRouter><ReviewsPage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("修改复盘");
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("删除这条复盘？");
    expect(screen.getByRole("button", { name: "确认删除" })).toBeInTheDocument();
  });
});

function fixedWeekPlan(state: "pending" | "completed") {
  return {
    plan_id: "plan-1",
    status: state === "completed" ? "completed" : "active",
    plan_date: "2026-08-10",
    horizon_end: "2026-08-16",
    weekly_focus: [{ week_index: 1, focus: "完成项目", success_signal: "形成可演示成果" }],
    tasks: Array.from({ length: 7 }, (_, index) => ({
      task_id: `task-${index}`,
      plan_id: "plan-1",
      scheduled_date: `2026-08-${String(10 + index).padStart(2, "0")}`,
      state,
    })),
  };
}

function mockReviewPage(plan: ReturnType<typeof fixedWeekPlan>): void {
  vi.mocked(useMe).mockReturnValue({
    data: { profile_complete: true, active_plan: plan },
  } as unknown as ReturnType<typeof useMe>);
  vi.mocked(useActivePlan).mockReturnValue({
    data: plan,
    isLoading: false,
  } as unknown as ReturnType<typeof useActivePlan>);
  vi.mocked(useReviews).mockReturnValue({
    data: {
      items: [{
        review_id: "review-1",
        plan_id: "plan-1",
        review_date: "2026-08-11",
        mood: 3,
        completed_count: 0,
        abandoned_count: 0,
        blockers: null,
        adjustment_request: null,
        free_text: null,
        companion_message: "先保持本周重点。",
        suggested_replan: false,
        next_plan_run_id: null,
        version: 1,
        created_at: "2026-08-11T04:00:00Z",
        updated_at: "2026-08-11T04:00:00Z",
      }],
    },
    isLoading: false,
  } as unknown as ReturnType<typeof useReviews>);
  vi.mocked(useStartNextPlan).mockReturnValue({
    isPending: false,
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useStartNextPlan>);
}
