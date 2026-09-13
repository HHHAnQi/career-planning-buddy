import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  AgentRunResponse,
} from "./types";

export function useRun(runId: string | undefined) {
  return useQuery({
    queryKey: ["runs", runId],
    queryFn: () => apiRequest<AgentRunResponse>(`/api/v1/agent-runs/${runId}`),
    enabled: runId !== undefined,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data === undefined) return false;
      // 终态后停止轮询；非终态每 1.2s 拉一次
      if (["completed", "degraded", "failed", "cancelled"].includes(data.status)) {
        return false;
      }
      return 1200;
    },
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      apiRequest<{ run_id: string; status: string; cancel_requested: boolean }>(
        `/api/v1/agent-runs/${runId}/cancel`,
        {
          method: "POST",
          body: { reason: "user_abort" },
          idempotencyKey: `cancel-${runId}`,
        }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}
