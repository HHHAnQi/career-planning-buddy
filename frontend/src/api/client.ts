/**
 * 统一 HTTP 客户端：base URL、JWT、Idempotency-Key、错误归一化。
 */

const TOKEN_KEY = "cpb_access_token";
const DEFAULT_TIMEOUT_MS = 15_000;

export function getAuthToken(): string | null {
  const sessionToken = sessionStorage.getItem(TOKEN_KEY);
  if (sessionToken !== null) return sessionToken;
  const legacyToken = localStorage.getItem(TOKEN_KEY);
  if (legacyToken !== null) {
    sessionStorage.setItem(TOKEN_KEY, legacyToken);
    localStorage.removeItem(TOKEN_KEY);
  }
  return legacyToken;
}

export function setAuthToken(token: string | null): void {
  if (token === null) {
    sessionStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
  } else {
    sessionStorage.setItem(TOKEN_KEY, token);
    localStorage.removeItem(TOKEN_KEY);
  }
}

export function apiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string | null,
    message: string,
    public readonly requestId: string | null
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown | FormData;
  idempotencyKey?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const {
    method = "GET",
    body,
    idempotencyKey,
    signal,
    timeoutMs = DEFAULT_TIMEOUT_MS,
  } = options;
  const token = getAuthToken();
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (token !== null) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (body !== undefined && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (idempotencyKey !== undefined) {
    headers["Idempotency-Key"] = idempotencyKey;
  }

  let response: Response;
  const requestController = new AbortController();
  const forwardAbort = (): void => requestController.abort(signal?.reason);
  if (signal?.aborted) forwardAbort();
  else signal?.addEventListener("abort", forwardAbort, { once: true });
  const timeoutId = window.setTimeout(
    () => requestController.abort("request-timeout"),
    timeoutMs,
  );
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
      signal: requestController.signal,
    });
  } catch (err) {
    if (requestController.signal.aborted && !signal?.aborted) {
      throw new ApiError(0, "REQUEST_TIMEOUT", "请求超时，请稍后重试", null);
    }
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    throw new ApiError(0, "NETWORK_UNREACHABLE", "网络请求失败", null);
  } finally {
    window.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", forwardAbort);
  }

  if (!response.ok) {
    const payload = await safeJson(response);
    const payloadRecord = isRecord(payload) ? payload : {};
    const errBody = isRecord(payloadRecord.error) ? payloadRecord.error : payloadRecord;
    throw new ApiError(
      response.status,
      (typeof errBody.code === "string" && errBody.code) || null,
      (typeof errBody.message === "string" && errBody.message) || `HTTP ${response.status}`,
      (typeof errBody.request_id === "string" && errBody.request_id) || null
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
