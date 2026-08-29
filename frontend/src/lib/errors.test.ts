import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "@/api/client";
import { toUserFacingError } from "@/lib/errors";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("toUserFacingError", () => {
  it("turns network failures into a recoverable product message", () => {
    const result = toUserFacingError(new ApiError(0, "NETWORK_UNREACHABLE", "raw", null));
    expect(result.title).toBe("网络似乎断开了");
    expect(result.action).toBe("retry");
    expect(result.message).not.toContain("NETWORK_UNREACHABLE");
  });

  it("does not expose an active-run error code to the user", () => {
    const result = toUserFacingError(
      new ApiError(409, "STATE_RUN_ALREADY_ACTIVE", "raw", "request-1"),
    );
    expect(result.title).toBe("已有一份路线正在生成");
    expect(result.action).toBe("view-progress");
    expect(result.requestId).toBe("request-1");
  });

  it("maps optimistic locking conflicts to refresh", () => {
    const result = toUserFacingError(new ApiError(409, "VERSION_CONFLICT", "raw", null));
    expect(result.action).toBe("refresh");
  });

  it("shows a fixed safety response for high-risk goal input", () => {
    const result = toUserFacingError(
      new ApiError(422, "SAFETY_HIGH_RISK_INPUT", "raw", "request-safe"),
    );
    expect(result.title).toBe("请先确保你现在是安全的");
    expect(result.message).toContain("当地紧急服务");
    expect(result.message).not.toContain("raw");
  });

  it("reads the shared backend error envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "STATE_RUN_ALREADY_ACTIVE",
              message: "active run exists",
              request_id: "request-1",
              details: {},
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await expect(apiRequest("/api/v1/agent-runs/run-1")).rejects.toMatchObject({
      status: 409,
      code: "STATE_RUN_ALREADY_ACTIVE",
      requestId: "request-1",
    });
  });
});
