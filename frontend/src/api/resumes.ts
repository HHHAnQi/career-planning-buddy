import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "./client";
import type { JobTargetResponse, ResumeAssessmentResponse, ResumeDocumentExtractResponse, ResumeOptimizationRunResponse, ResumeRewriteBatchApplyResponse, ResumeRewriteDecisionResponse, ResumeVersionResponse } from "./types";

export function useResumeVersions() {
  return useQuery({
    queryKey: ["resume-versions"],
    queryFn: () => apiRequest<{ items: ResumeVersionResponse[] }>("/api/v1/resume-versions"),
  });
}

export function useCreateResumeAssessment() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { resume_version_id: string; job_target_id: string; interview_session_id?: string | null }) =>
      apiRequest<ResumeOptimizationRunResponse>("/api/v1/resume-assessments/optimize", {
        method: "POST", body, idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["resume-assessments"] }),
  });
}

export function useApplyResumeRewritesBatch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { assessmentId: string; claimIds: string[] }) =>
      apiRequest<ResumeRewriteBatchApplyResponse>(`/api/v1/resume-assessments/${input.assessmentId}/rewrites/apply-batch`, {
        method: "POST",
        body: { claim_ids: input.claimIds },
      }),
    onSuccess: async () => Promise.all([
      client.invalidateQueries({ queryKey: ["resume-assessments"] }),
      client.invalidateQueries({ queryKey: ["resume-versions"] }),
    ]),
  });
}

export function useResumeAssessments() {
  return useQuery({
    queryKey: ["resume-assessments"],
    queryFn: () => apiRequest<ResumeAssessmentResponse[]>("/api/v1/resume-assessments"),
  });
}

export function useResumeAssessment(assessmentId: string | undefined) {
  return useQuery({
    queryKey: ["resume-assessments", assessmentId],
    queryFn: () => apiRequest<ResumeAssessmentResponse>(`/api/v1/resume-assessments/${assessmentId}`),
    enabled: assessmentId !== undefined,
  });
}

export function useDecideResumeRewrite() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { assessmentId: string; claimId: string; status: "accepted" | "rejected"; rewriteText?: string }) =>
      apiRequest<ResumeRewriteDecisionResponse>(`/api/v1/resume-assessments/${input.assessmentId}/claims/${input.claimId}/decision`, {
        method: "PUT",
        body: { status: input.status, rewrite_text: input.status === "accepted" ? input.rewriteText : null },
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["resume-assessments"] }),
  });
}

export function useJobTargets() {
  return useQuery({
    queryKey: ["job-targets"],
    queryFn: () => apiRequest<{ items: JobTargetResponse[] }>("/api/v1/job-targets"),
  });
}

export function useCreateResumeVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      label: string;
      source_text: string;
      source_type?: "pasted_text" | "uploaded_file";
      source_filename?: string;
      source_media_type?: string;
      parent_version_id?: string;
    }) =>
      apiRequest<ResumeVersionResponse>("/api/v1/resume-versions", {
        method: "POST",
        body,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["resume-versions"] }),
  });
}

export function useExtractResumeDocument() {
  return useMutation({
    mutationFn: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return apiRequest<ResumeDocumentExtractResponse>("/api/v1/resume-versions/extract", {
        method: "POST",
        body,
        timeoutMs: 30_000,
      });
    },
  });
}

export function useCreateJobTarget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { title: string; company?: string; jd_text: string }) =>
      apiRequest<JobTargetResponse>("/api/v1/job-targets", {
        method: "POST",
        body,
        idempotencyKey: crypto.randomUUID(),
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["job-targets"] }),
  });
}

export function useDeleteResumeVersion() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (resumeId: string) => apiRequest<void>(`/api/v1/resume-versions/${resumeId}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["resume-versions"] }),
  });
}

export function useDeleteJobTarget() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (targetId: string) => apiRequest<void>(`/api/v1/job-targets/${targetId}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["job-targets"] }),
  });
}
