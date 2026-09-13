import { afterEach, describe, expect, it, vi } from "vitest";

import {
  reconnectDelayMs,
  SseHttpError,
  streamRunEvents,
  type RunStreamEvent,
} from "./sse";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("streamRunEvents", () => {
  it("uses an Authorization header, resumes by header, and stops on terminal event", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'id: 8\nevent: progress\ndata: {"message":"正在生成"}\n\n' +
              'id: 9\nevent: run.completed\ndata: {"status":"completed"}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const events: RunStreamEvent[] = [];

    const lastEventId = await streamRunEvents("run-1", {
      token: "long-lived-token",
      lastEventId: 7,
      signal: new AbortController().signal,
      onEvent: (event) => {
        events.push(event);
        return event.type !== "run.completed";
      },
    });

    expect(lastEventId).toBe(9);
    expect(events.map((event) => event.type)).toEqual(["progress", "run.completed"]);
    const call = fetchMock.mock.calls[0];
    expect(call).toBeDefined();
    if (call === undefined) throw new Error("expected one SSE request");
    const [url, init] = call;
    expect(String(url)).toBe("/api/v1/agent-runs/run-1/events");
    expect(String(url)).not.toContain("access_token");
    expect(init?.headers).toMatchObject({
      Authorization: "Bearer long-lived-token",
      "Last-Event-ID": "7",
    });
  });

  it("surfaces HTTP status and computes bounded jittered backoff", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 401 })));
    await expect(
      streamRunEvents("run-1", {
        token: "expired",
        signal: new AbortController().signal,
        onEvent: () => undefined,
      }),
    ).rejects.toEqual(new SseHttpError(401));
    expect(reconnectDelayMs(1, () => 0.5)).toBe(1000);
    expect(reconnectDelayMs(20, () => 0.5)).toBe(30_000);
  });
});
