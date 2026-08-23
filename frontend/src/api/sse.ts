import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiBaseUrl, getAuthToken, setAuthToken } from "./client";

const TERMINAL_EVENTS = new Set([
  "run.completed",
  "run.degraded",
  "run.failed",
  "run.cancelled",
]);

export type RunConnectionState = "idle" | "connecting" | "live" | "reconnecting" | "closed";

export interface RunStreamState {
  connectionState: RunConnectionState;
  progressMessage: string | null;
}

export interface RunStreamEvent {
  id: number | null;
  type: string;
  data: unknown;
}

export class SseHttpError extends Error {
  constructor(public readonly status: number) {
    super(`SSE request failed with HTTP ${status}`);
    this.name = "SseHttpError";
  }
}

const MAX_RECONNECT_ATTEMPTS = 8;

export function reconnectDelayMs(attempt: number, random = Math.random): number {
  const base = Math.min(1000 * 2 ** Math.max(0, attempt - 1), 30_000);
  return Math.round(base * (0.8 + random() * 0.4));
}

interface StreamRunEventsOptions {
  token: string;
  lastEventId?: number;
  signal: AbortSignal;
  onOpen?: () => void;
  onEvent: (event: RunStreamEvent) => boolean | void;
}

/** Read a durable SSE stream with a Bearer header and return the last event id seen. */
export async function streamRunEvents(
  runId: string,
  options: StreamRunEventsOptions,
): Promise<number | undefined> {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    Authorization: `Bearer ${options.token}`,
  };
  if (options.lastEventId !== undefined) {
    headers["Last-Event-ID"] = String(options.lastEventId);
  }

  const response = await fetch(`${apiBaseUrl()}/api/v1/agent-runs/${runId}/events`, {
    headers,
    signal: options.signal,
  });
  if (!response.ok) {
    throw new SseHttpError(response.status);
  }
  if (response.body === null) {
    throw new Error("SSE response body is unavailable");
  }

  options.onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let lastEventId = options.lastEventId;

  while (true) {
    const chunk = await reader.read();
    buffer += decoder.decode(chunk.value, { stream: !chunk.done });
    buffer = buffer.replace(/\r\n/g, "\n");

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = parseEventBlock(block);
      if (event !== null) {
        if (event.id !== null) lastEventId = event.id;
        if (options.onEvent(event) === false) {
          await reader.cancel();
          return lastEventId;
        }
      }
      boundary = buffer.indexOf("\n\n");
    }

    if (chunk.done) return lastEventId;
  }
}

function parseEventBlock(block: string): RunStreamEvent | null {
  let id: number | null = null;
  let type = "message";
  const dataLines: string[] = [];

  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator >= 0 ? line.slice(0, separator) : line;
    const rawValue = separator >= 0 ? line.slice(separator + 1) : "";
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;
    if (field === "id" && /^\d+$/.test(value)) id = Number(value);
    if (field === "event" && value.length > 0) type = value;
    if (field === "data") dataLines.push(value);
  }

  if (dataLines.length === 0 && type === "message" && id === null) return null;
  const rawData = dataLines.join("\n");
  let data: unknown = rawData;
  if (rawData.length > 0) {
    try {
      data = JSON.parse(rawData) as unknown;
    } catch {
      data = rawData;
    }
  }
  return { id, type, data };
}

export function useRunEventStream(runId: string | undefined): RunStreamState {
  const queryClient = useQueryClient();
  const [connectionState, setConnectionState] = useState<RunConnectionState>("idle");
  const [progressMessage, setProgressMessage] = useState<string | null>(null);

  useEffect(() => {
    if (runId === undefined) {
      setConnectionState("idle");
      setProgressMessage(null);
      return;
    }
    const token = getAuthToken();
    if (token === null) {
      setConnectionState("idle");
      return;
    }

    const controller = new AbortController();
    let stopped = false;
    let terminalReceived = false;
    let lastEventId: number | undefined;
    let reconnectAttempts = 0;

    const invalidateRun = (): void => {
      void queryClient.invalidateQueries({ queryKey: ["runs", runId] });
    };
    const invalidatePlanState = (): void => {
      void queryClient.invalidateQueries({ queryKey: ["plans"] });
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    };

    const connect = async (): Promise<void> => {
      setConnectionState("connecting");
      while (!stopped && !terminalReceived) {
        try {
          lastEventId = await streamRunEvents(runId, {
            token,
            lastEventId,
            signal: controller.signal,
            onOpen: () => {
              if (!stopped) setConnectionState("live");
            },
            onEvent: (event) => {
              reconnectAttempts = 0;
              invalidateRun();
              if (event.type === "progress" && isRecord(event.data)) {
                const message = event.data.message;
                if (typeof message === "string") setProgressMessage(message);
              }
              if (event.type === "llm.stream.progress" && isRecord(event.data)) {
                const chars = event.data.cumulative_chars;
                if (typeof chars === "number") {
                  setProgressMessage(`模型生成中 · 已输出 ${chars} 字符`);
                }
              }
              if (event.type === "plan.ready") invalidatePlanState();
              if (event.type.startsWith("interview.")) {
                void queryClient.invalidateQueries({ queryKey: ["interviews"] });
              }
              if (TERMINAL_EVENTS.has(event.type)) {
                terminalReceived = true;
                invalidatePlanState();
                setConnectionState("closed");
                return false;
              }
              return true;
            },
          });
        } catch (error: unknown) {
          if (controller.signal.aborted) return;
          if (error instanceof DOMException && error.name === "AbortError") return;
          if (error instanceof SseHttpError && error.status === 401) {
            setAuthToken(null);
            setConnectionState("closed");
            return;
          }
        }
        if (!stopped && !terminalReceived) {
          reconnectAttempts += 1;
          if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
            setConnectionState("closed");
            return;
          }
          setConnectionState("reconnecting");
          await waitForReconnect(
            reconnectDelayMs(reconnectAttempts),
            controller.signal,
          );
        }
      }
    };

    void connect();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [runId, queryClient]);

  return { connectionState, progressMessage };
}

function waitForReconnect(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timeoutId = window.setTimeout(done, delayMs);
    function done(): void {
      window.clearTimeout(timeoutId);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done, { once: true });
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
