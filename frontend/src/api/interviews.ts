import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type {
  InterviewComparison,
  InterviewRunResponse,
  InterviewSessionResponse,
  TrainingActionsConfirmResponse,
  TrainingActionsPreviewResponse,
} from "./types";

export function useInterviews() {
  return useQuery({
    queryKey: ["interviews"],
    queryFn: () => apiRequest<{ items: InterviewSessionResponse[] }>("/api/v1/interviews"),
  });
}

export function useInterview(interviewId: string | undefined) {
  return useQuery({
    queryKey: ["interviews", interviewId],
    queryFn: () => apiRequest<InterviewSessionResponse>(`/api/v1/interviews/${interviewId}`),
    enabled: interviewId !== undefined,
    refetchInterval: (query) => {
      const session = query.state.data;
      const currentTurn = session?.turns.find((turn) => turn.turn_id === session.current_turn_id);
      return session?.active_run || session?.status === "report_generating" || currentTurn?.analysis_status === "running" ? 1200 : false;
    },
  });
}

export function useCreateInterview() {
  return useMutation({
    mutationFn: (body: { resume_version_id: string; job_target_id: string; interview_type: string; question_limit: number; followup_limit: number }) =>
      apiRequest<InterviewRunResponse>("/api/v1/interviews", {
        method: "POST", body, idempotencyKey: crypto.randomUUID(),
      }),
  });
}

export function useDeleteInterview() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (interviewId: string) => apiRequest<void>(`/api/v1/interviews/${interviewId}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["interviews"] }),
  });
}

function useInterviewAction(path: (args: ActionArgs) => string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: ActionArgs) => apiRequest<InterviewRunResponse>(path(args), {
      method: "POST",
      body: args.body,
      idempotencyKey: crypto.randomUUID(),
    }),
    onSuccess: (data) => client.invalidateQueries({ queryKey: ["interviews", data.interview_id] }),
  });
}

interface ActionArgs { interviewId: string; body: unknown; turnId?: string }

export function useSubmitInterviewAnswer() {
  return useInterviewAction(({ interviewId }) => `/api/v1/interviews/${interviewId}/answers`);
}

export function useSubmitInterviewAudio() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { interviewId: string; turnId: string; version: number; audio: File; fallbackText: string }) => {
      const body = new FormData();
      body.append("turn_id", args.turnId);
      body.append("version", String(args.version));
      body.append("audio", args.audio);
      if (args.fallbackText.trim()) body.append("fallback_text", args.fallbackText.trim());
      return apiRequest<InterviewRunResponse>(`/api/v1/interviews/${args.interviewId}/audio-answers`, {
        method: "POST", body, idempotencyKey: crypto.randomUUID(), timeoutMs: 65_000,
      });
    },
    onSuccess: (data) => client.invalidateQueries({ queryKey: ["interviews", data.interview_id] }),
  });
}

export function useRetryInterviewStart() {
  return useInterviewAction(({ interviewId }) => `/api/v1/interviews/${interviewId}/start/retry`);
}

export function useSkipInterviewTurn() {
  return useInterviewAction(({ interviewId, turnId }) => `/api/v1/interviews/${interviewId}/turns/${turnId}/skip`);
}

export function useFinishInterview() {
  return useInterviewAction(({ interviewId }) => `/api/v1/interviews/${interviewId}/finish`);
}

export function useRetryInterviewReport() {
  return useInterviewAction(({ interviewId }) => `/api/v1/interviews/${interviewId}/report/retry`);
}

export function useCreateInterviewMemoryCandidates() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { interviewId: string; weaknessKeys: string[] }) =>
      apiRequest<{ created_candidate_ids: string[]; eligible_weakness_keys: string[]; skipped_weakness_keys: string[] }>(
        `/api/v1/interviews/${args.interviewId}/memory-candidates`,
        { method: "POST", body: { weakness_keys: args.weaknessKeys } },
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ["memory-candidates"] }),
  });
}

export function usePreviewInterviewTraining() {
  return useMutation({
    mutationFn: (args: { interviewId: string; actionIndexes: number[] }) =>
      apiRequest<TrainingActionsPreviewResponse>(
        `/api/v1/interviews/${args.interviewId}/training-actions/preview`,
        { method: "POST", body: { action_indexes: args.actionIndexes } },
      ),
  });
}

export function useConfirmInterviewTraining() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (args: { interviewId: string; actionIndexes: number[] }) =>
      apiRequest<TrainingActionsConfirmResponse>(
        `/api/v1/interviews/${args.interviewId}/training-actions/confirm`,
        {
          method: "POST",
          body: { action_indexes: args.actionIndexes },
          idempotencyKey: crypto.randomUUID(),
        },
      ),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["plans"] });
      void client.invalidateQueries({ queryKey: ["active-plan"] });
    },
  });
}

export function useCreateRetest() {
  return useMutation({
    mutationFn: (args: { interviewId: string; weaknessKeys: string[] }) =>
      apiRequest<InterviewRunResponse>(`/api/v1/interviews/${args.interviewId}/retest`, {
        method: "POST",
        body: { weakness_keys: args.weaknessKeys },
        idempotencyKey: crypto.randomUUID(),
      }),
  });
}

export function useInterviewComparison(interviewId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["interviews", interviewId, "comparison"],
    queryFn: () => apiRequest<InterviewComparison>(`/api/v1/interviews/${interviewId}/comparison`),
    enabled: interviewId !== undefined && enabled,
  });
}
