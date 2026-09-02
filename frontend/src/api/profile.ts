import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  ProfilePatchRequest,
  ProfilePutRequest,
  ProfileResponse,
} from "./types";

const KEY = ["profile"] as const;

export function useProfile() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => apiRequest<ProfileResponse>("/api/v1/profile"),
    retry: false,
  });
}

export function usePutProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: ProfilePutRequest; idempotencyKey: string }) =>
      apiRequest<ProfileResponse>("/api/v1/profile", {
        method: "PUT",
        body: payload,
        idempotencyKey,
      }),
    onSuccess: (data) => {
      qc.setQueryData(KEY, data);
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function usePatchProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, idempotencyKey }: { payload: ProfilePatchRequest; idempotencyKey: string }) =>
      apiRequest<ProfileResponse>("/api/v1/profile", {
        method: "PATCH",
        body: payload,
        idempotencyKey,
      }),
    onSuccess: (data) => {
      qc.setQueryData(KEY, data);
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}
