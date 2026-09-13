import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  ReviewCreateRequest,
  ReviewResponse,
  ReviewUpdateRequest,
  StartNextPlanResponse,
} from "./types";

export function useReviews() {
  return useQuery({
    queryKey: ["reviews"],
    queryFn: () => apiRequest<{ items: ReviewResponse[] }>(`/api/v1/reviews`),
    retry: false,
  });
}

export function useCreateReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: ReviewCreateRequest;
      idempotencyKey: string;
    }) =>
      apiRequest<ReviewResponse>("/api/v1/reviews", {
        method: "POST",
        body: payload,
        idempotencyKey,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviews"] });
    },
  });
}

export function useReview(reviewId: string | undefined) {
  return useQuery({
    queryKey: ["reviews", reviewId],
    queryFn: () => apiRequest<ReviewResponse>(`/api/v1/reviews/${reviewId}`),
    enabled: reviewId !== undefined,
    retry: false,
  });
}

export function useUpdateReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ reviewId, payload }: { reviewId: string; payload: ReviewUpdateRequest }) =>
      apiRequest<ReviewResponse>(`/api/v1/reviews/${reviewId}`, {
        method: "PATCH",
        body: payload,
      }),
    onSuccess: (review) => {
      qc.setQueryData(["reviews", review.review_id], review);
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useDeleteReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (reviewId: string) =>
      apiRequest<void>(`/api/v1/reviews/${reviewId}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useStartNextPlan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      reviewId,
      idempotencyKey,
    }: {
      reviewId: string;
      idempotencyKey: string;
    }) =>
      apiRequest<StartNextPlanResponse>(
        `/api/v1/reviews/${reviewId}/start-next-plan`,
        { method: "POST", body: {}, idempotencyKey }
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runs"] });
      qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}
