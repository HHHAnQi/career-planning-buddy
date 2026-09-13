import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type { GoalBriefConfirmResponse, GoalBriefCreateRequest, GoalBriefResponse } from "./types";

export function useCreateGoalBrief() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: GoalBriefCreateRequest; idempotencyKey: string }) =>
      apiRequest<GoalBriefResponse>("/api/v1/goal-briefs", { method: "POST", body: payload, idempotencyKey }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}

export function useRefineGoalBrief() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ briefId, version, message }: { briefId: string; version: number; message: string }) =>
      apiRequest<GoalBriefResponse>(`/api/v1/goal-briefs/${briefId}/refine`, { method: "POST", body: { version, message } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}

export function useConfirmGoalBrief() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ briefId, version }: { briefId: string; version: number }) =>
      apiRequest<GoalBriefConfirmResponse>(`/api/v1/goal-briefs/${briefId}/confirm`, { method: "POST", body: { version } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}

export function useCancelGoalBrief() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ briefId, version }: { briefId: string; version: number }) =>
      apiRequest<GoalBriefResponse>(`/api/v1/goal-briefs/${briefId}/cancel`, { method: "POST", body: { version } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }),
  });
}
