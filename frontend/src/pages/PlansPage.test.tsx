import { describe, expect, it } from "vitest";

import type { ActivePlanResponse } from "@/api/types";
import { currentWeek, executionProgress } from "./PlansPage";

const plan = {
  plan_id: "plan-1",
  status: "generated",
  plan_date: "2026-08-11",
  horizon_start: "2026-08-11",
  horizon_end: "2026-09-07",
  overall_direction: "完成 Agent 项目",
  weekly_focus: [
    { week_index: 1, focus: "定义范围", success_signal: "形成清单" },
    { week_index: 2, focus: "实现闭环", success_signal: "可以演示" },
    { week_index: 3, focus: "完善质量", success_signal: "测试通过" },
    { week_index: 4, focus: "整理表达", success_signal: "完成复盘" },
  ],
  summary: "四周完成一个项目",
  rationale: "按周推进",
  adjustment_reason: null,
  sources: [],
  tasks: [],
  companion_message: null,
  version: 5,
  adopted_at: null,
  created_at: "2026-08-11T00:00:00Z",
} satisfies ActivePlanResponse;

describe("route progress", () => {
  it("shows the first day as 1/7 instead of 100 percent", () => {
    expect(executionProgress(plan, "2026-08-11")).toEqual({
      elapsed: 1,
      total: 7,
      percent: 14,
      end: "2026-08-17",
    });
    expect(currentWeek(plan, "2026-08-11")).toBe(1);
  });

  it("shows a future cycle as not started", () => {
    const future = { ...plan, plan_date: "2026-08-19", horizon_start: "2026-08-19" };
    expect(executionProgress(future, "2026-08-11")).toEqual({
      elapsed: 0,
      total: 7,
      percent: 0,
      end: "2026-08-25",
    });
  });

  it("uses the shorter final period as the denominator", () => {
    const finalPeriod = { ...plan, plan_date: "2026-08-19", horizon_start: "2026-08-19", horizon_end: "2026-08-21" };
    expect(executionProgress(finalPeriod, "2026-08-19")).toEqual({
      elapsed: 1,
      total: 3,
      percent: 33,
      end: "2026-08-21",
    });
  });
});
