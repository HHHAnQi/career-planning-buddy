export interface HealthResponse {
  status: "ok";
  service: string;
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;
  return candidate.status === "ok" && typeof candidate.service === "string";
}

function healthUrl(): string {
  const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
  return `${baseUrl}/health`;
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(healthUrl(), {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }

  const payload: unknown = await response.json();
  if (!isHealthResponse(payload)) {
    throw new Error("Health check returned an invalid response");
  }
  return payload;
}
