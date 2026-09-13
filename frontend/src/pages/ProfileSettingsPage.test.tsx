import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useCreateGoalBrief } from "@/api/goal-briefs";
import { useMe } from "@/api/auth";
import { usePlans } from "@/api/plans";
import { usePatchProfile, useProfile } from "@/api/profile";
import type { ProfileResponse } from "@/api/types";
import { ProfileSettingsPage } from "./ProfileSettingsPage";

vi.mock("@/api/goal-briefs", () => ({ useCreateGoalBrief: vi.fn() }));
vi.mock("@/api/auth", () => ({ useMe: vi.fn() }));
vi.mock("@/api/plans", () => ({ usePlans: vi.fn() }));
vi.mock("@/api/profile", () => ({ usePatchProfile: vi.fn(), useProfile: vi.fn() }));

const profile: ProfileResponse = {
  goal_type: "agent_app",
  stage: "preparing",
  time_budget_minutes: 60,
  skill_level: "intermediate",
  skill_summary: "已经完成 Agent 项目主链路",
  start_date: "2026-08-11",
  deadline: "2026-09-30",
  preferences: { target_companies: [], preferred_time_slot: null, weekly_available_days: [] },
  version: 1,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ProfileSettingsPage", () => {
  it("saves the new profile and explicitly starts a progress-aware replan", async () => {
    const patch = vi.fn().mockResolvedValue({ ...profile, time_budget_minutes: 90, version: 2 });
    const createGoalBrief = vi.fn().mockResolvedValue({ goal_brief_id: "brief-2" });
    vi.mocked(useProfile).mockReturnValue({
      data: profile,
      isLoading: false,
    } as ReturnType<typeof useProfile>);
    vi.mocked(useMe).mockReturnValue({
      data: { active_plan: null },
    } as unknown as ReturnType<typeof useMe>);
    vi.mocked(usePlans).mockReturnValue({
      data: { items: [{ plan_id: "plan-1", status: "completed" }], next_cursor: null },
    } as unknown as ReturnType<typeof usePlans>);
    vi.mocked(usePatchProfile).mockReturnValue({
      error: null,
      isPending: false,
      mutateAsync: patch,
    } as unknown as ReturnType<typeof usePatchProfile>);
    vi.mocked(useCreateGoalBrief).mockReturnValue({
      error: null,
      isPending: false,
      mutateAsync: createGoalBrief,
    } as unknown as ReturnType<typeof useCreateGoalBrief>);

    render(<MemoryRouter><ProfileSettingsPage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "90 分钟" }));
    fireEvent.click(screen.getByRole("button", { name: "保存并整理重新规划目标" }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith(expect.objectContaining({
      payload: expect.objectContaining({
        time_budget_minutes: 90,
        start_date: "2026-08-11",
        deadline: "2026-09-30",
      }),
    })));
    await waitFor(() => expect(createGoalBrief).toHaveBeenCalledWith(expect.objectContaining({
      payload: expect.objectContaining({
        hint_intent: "replan",
        source_plan_id: "plan-1",
        message: expect.stringContaining("已经完成、进行中和放弃"),
      }),
    })));
  });
});
