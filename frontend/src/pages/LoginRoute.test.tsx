import { StrictMode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginRoute, RequireAuth, RequireDev } from "./LoginRoute";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("RequireAuth", () => {
  it("redirects unauthenticated users to the login page under StrictMode", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={["/today"]}>
            <Routes>
              <Route path="/login" element={<p>登录页面</p>} />
              <Route element={<RequireAuth requireProfile />}>
                <Route path="/today" element={<p>受保护的今日页面</p>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </StrictMode>,
    );

    expect(await screen.findByText("登录页面")).toBeInTheDocument();
    expect(screen.queryByText("受保护的今日页面")).not.toBeInTheDocument();
  });
});

describe("LoginRoute", () => {
  it("logs in with email and restores the same authenticated account", async () => {
    let loginRequests = 0;
    let meRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/v1/auth/login")) {
          loginRequests += 1;
          expect(JSON.parse(String(init?.body))).toEqual({
            email: "anqi@example.com",
            password: "password123",
          });
          return new Response(
            JSON.stringify({
              access_token: "email-token",
              token_type: "bearer",
              expires_in: 3600,
              user: {
                id: "user-1",
                email: "anqi@example.com",
                display_name: "AnQi",
                role: "user",
              },
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        if (url.endsWith("/api/v1/me")) {
          meRequests += 1;
          return new Response(
            JSON.stringify({
              user: {
                id: "user-1",
                email: "anqi@example.com",
                display_name: "AnQi",
                role: "user",
              },
              profile_complete: true,
              planning_window_valid: true,
              profile: null,
              active_plan: null,
              today_tasks: [],
              latest_review: null,
              active_run: null,
              active_goal_brief: null,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(null, { status: 404 });
      }),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[{ pathname: "/login", state: { from: { pathname: "/materials" } } }]}>
          <Routes>
            <Route path="/login" element={<LoginRoute />} />
            <Route path="/materials" element={<p>求职材料</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.change(screen.getByLabelText("邮箱"), {
      target: { value: "AnQi@example.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "password123" },
    });
    const submitButton = screen.getAllByRole("button", { name: "登录" }).at(1);
    expect(submitButton).toBeDefined();
    fireEvent.click(submitButton!);

    expect(await screen.findByText("求职材料")).toBeInTheDocument();
    expect(loginRequests).toBe(1);
    expect(meRequests).toBe(1);
    expect(sessionStorage.getItem("cpb_access_token")).toBe("email-token");
  });
});

describe("RequireDev", () => {
  it("redirects a normal authenticated user away from developer routes", async () => {
    localStorage.setItem("cpb_access_token", "user-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(
        JSON.stringify({
          user: { id: "user-1", email: "user@example.com", display_name: null, role: "user" },
          profile_complete: true,
          planning_window_valid: true,
          profile: null,
          active_plan: null,
          today_tasks: [],
          latest_review: null,
          active_run: null,
          active_goal_brief: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/dev/evals"]}>
          <Routes>
            <Route element={<RequireDev />}>
              <Route path="/dev/evals" element={<p>开发者评测</p>} />
            </Route>
            <Route path="/me" element={<p>我的页面</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("我的页面")).toBeInTheDocument();
    expect(screen.queryByText("开发者评测")).not.toBeInTheDocument();
  });
});
