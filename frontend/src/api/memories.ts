import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  MemoryCandidateDecisionResponse,
  MemoryCandidateResponse,
  MemoryResponse,
} from "./types";

export function useMemories(status: "active" | "closed" = "active") {
  return useQuery({
    queryKey: ["memories", status],
    queryFn: () => apiRequest<{ items: MemoryResponse[] }>(`/api/v1/memories?status=${status}`),
    retry: false,
  });
}

export function useMemoryCandidates() {
  return useQuery({
    queryKey: ["memory-candidates"],
    queryFn: () =>
      apiRequest<{ items: MemoryCandidateResponse[] }>(`/api/v1/memory-candidates`),
    retry: false,
  });
}

export function useDecideCandidate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ candidateId, decision }: { candidateId: string; decision: "confirm" | "reject" }) =>
      apiRequest<MemoryCandidateDecisionResponse>(
        `/api/v1/memory-candidates/${candidateId}/${decision}`,
        {
          method: "POST",
          body: {},
          idempotencyKey: `memory-${decision}-${candidateId}`,
        }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory-candidates"] });
      qc.invalidateQueries({ queryKey: ["memories"] });
    },
  });
}

export function useDeleteMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (memoryId: string) =>
      apiRequest<void>(`/api/v1/memories/${memoryId}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memories"] });
    },
  });
}

export function usePatchMemory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      memoryId,
      payload,
    }: {
      memoryId: string;
      payload: { status: "active" | "closed"; version: number };
    }) =>
      apiRequest<MemoryResponse>(`/api/v1/memories/${memoryId}`, {
        method: "PATCH",
        body: payload,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memories"] });
    },
  });
}
