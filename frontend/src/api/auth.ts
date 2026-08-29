import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest, getAuthToken, setAuthToken } from "./client";
import type { GuestLoginResponse, MeResponse } from "./types";

const DEVICE_KEY = "cpb_device_id";
let guestLoginInFlight: Promise<GuestLoginResponse> | null = null;

function getOrCreateDeviceId(): string {
  const existing = localStorage.getItem(DEVICE_KEY);
  if (existing !== null) return existing;
  const created = crypto.randomUUID();
  localStorage.setItem(DEVICE_KEY, created);
  return created;
}

export function useGuestLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: loginGuestOnce,
    onSuccess: (data) => {
      setAuthToken(data.access_token);
      qc.removeQueries({ queryKey: ["me"], exact: true });
    },
  });
}

interface EmailLoginInput {
  email: string;
  password: string;
}

interface EmailRegisterInput extends EmailLoginInput {
  display_name?: string | null;
}

export function useEmailLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EmailLoginInput) => apiRequest<GuestLoginResponse>("/api/v1/auth/login", {
      method: "POST",
      body,
    }),
    onSuccess: (data) => {
      setAuthToken(data.access_token);
      qc.removeQueries({ queryKey: ["me"], exact: true });
    },
  });
}

export function useEmailRegister() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EmailRegisterInput) => apiRequest<GuestLoginResponse>("/api/v1/auth/register", {
      method: "POST",
      body,
    }),
    onSuccess: (data) => {
      setAuthToken(data.access_token);
      qc.removeQueries({ queryKey: ["me"], exact: true });
    },
  });
}

async function loginGuestOnce(): Promise<GuestLoginResponse> {
  if (guestLoginInFlight !== null) return guestLoginInFlight;
  const request = apiRequest<GuestLoginResponse>("/api/v1/auth/guest", {
    method: "POST",
    body: { device_id: getOrCreateDeviceId() },
  });
  guestLoginInFlight = request;
  try {
    return await request;
  } finally {
    if (guestLoginInFlight === request) guestLoginInFlight = null;
  }
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => apiRequest<MeResponse>("/api/v1/me"),
    enabled: getAuthToken() !== null,
    retry: false,
  });
}

export function useDeleteMe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiRequest<void>("/api/v1/me", { method: "DELETE" }),
    onSuccess: () => {
      setAuthToken(null);
      localStorage.removeItem(DEVICE_KEY);
      qc.clear();
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return () => {
    setAuthToken(null);
    qc.clear();
  };
}
