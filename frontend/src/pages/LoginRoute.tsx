import { useEffect, useState, type FormEvent } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useEmailLogin, useEmailRegister, useMe } from "@/api/auth";
import { ApiError, getAuthToken, setAuthToken } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginRoute(): JSX.Element {
  const location = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const emailLogin = useEmailLogin();
  const emailRegister = useEmailRegister();
  const me = useMe();
  const hasStoredToken = getAuthToken() !== null;
  const isUnauthorized = me.error instanceof ApiError && me.error.status === 401;
  const isSubmitting = emailLogin.isPending || emailRegister.isPending;
  const authError = emailLogin.error ?? emailRegister.error;
  const from = typeof location.state === "object"
    && location.state !== null
    && "from" in location.state
    ? location.state.from
    : null;
  const targetPath = typeof from === "object"
    && from !== null
    && "pathname" in from
    && typeof from.pathname === "string"
    ? from.pathname
    : null;

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const normalizedEmail = email.trim().toLowerCase();
    const normalizedName = displayName.trim();
    if (mode === "register") {
      emailRegister.mutate({
        email: normalizedEmail,
        password,
        display_name: normalizedName === "" ? null : normalizedName,
      });
    } else {
      emailLogin.mutate({ email: normalizedEmail, password });
    }
  }

  useEffect(() => {
    if (hasStoredToken && isUnauthorized) {
      setAuthToken(null);
    }
  }, [hasStoredToken, isUnauthorized]);

  if (me.data !== undefined && me.data !== null) {
    return (
      <Navigate
        to={targetPath ?? (me.data.profile_complete ? "/workspace" : "/onboarding")}
        replace
      />
    );
  }

  if (me.isError && !isUnauthorized && hasStoredToken) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>暂时无法加载账号</CardTitle>
            <CardDescription>
              登录状态存在，但用户信息加载失败。请稍后重试；如果问题持续，请检查后端日志。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={() => void me.refetch()}>重试加载</Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (hasStoredToken && (me.isLoading || me.data === undefined)) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <span className="text-sm text-muted-foreground">正在恢复你的账号…</span>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-6">
      <Card className="w-full max-w-md">
        <CardHeader className="space-y-2">
          <CardTitle>登录求职搭子</CardTitle>
          <CardDescription>
            使用同一个账号登录后，求职材料、面试记录、计划和记忆会保持一致。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-5 grid grid-cols-2 rounded-md border bg-background p-1">
            <button
              type="button"
              className={`rounded px-3 py-2 text-sm ${mode === "login" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
              onClick={() => setMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              className={`rounded px-3 py-2 text-sm ${mode === "register" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
              onClick={() => setMode("register")}
            >
              注册
            </button>
          </div>
          <form className="space-y-4" onSubmit={submit}>
            {mode === "register" && (
              <div className="space-y-2">
                <Label htmlFor="display-name">昵称</Label>
                <Input
                  id="display-name"
                  value={displayName}
                  maxLength={64}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="例如 AnQi"
                />
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="email">邮箱</Label>
              <Input
                id="email"
                type="email"
                value={email}
                autoComplete="email"
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@example.com"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                value={password}
                minLength={8}
                maxLength={128}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </div>
            {authError instanceof ApiError && (
              <p className="text-sm text-destructive">
                {authError.code === "AUTH_EMAIL_EXISTS"
                  ? "这个邮箱已经注册，请直接登录。"
                  : authError.code === "AUTH_INVALID_CREDENTIALS"
                    ? "邮箱或密码不正确。"
                    : authError.message}
              </p>
            )}
            <Button className="w-full" disabled={isSubmitting}>
              {isSubmitting ? "正在处理…" : mode === "login" ? "登录" : "创建账号"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

interface RequireAuthProps {
  requireProfile?: boolean;
}

/**
 * 守卫：要求显式账号登录，并按 profile 完整度定向。
 * - requireProfile=true：profile 不完整 → /onboarding
 * - 所有受保护路由无 token → /login
 */
export function RequireAuth({ requireProfile = false }: RequireAuthProps): JSX.Element {
  const location = useLocation();
  const me = useMe();
  const hasStoredToken = getAuthToken() !== null;

  if (!hasStoredToken) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (me.isError && me.error !== null) {
    if (me.error instanceof ApiError && me.error.status === 401) {
      setAuthToken(null);
    }
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (me.isLoading || me.data === undefined) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        正在加载…
      </div>
    );
  }

  // profile 是否完整决定是否能进 requireProfile 页
  if (requireProfile && me.data !== undefined && !me.data.profile_complete) {
    return <Navigate to="/onboarding" replace />;
  }

  return <Outlet />;
}

export function RequireProfile(): JSX.Element {
  const me = useMe();

  if (me.isLoading || me.data === undefined || me.data === null) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        正在准备你的工作台…
      </div>
    );
  }
  if (!me.data.profile_complete) {
    return <Navigate to="/onboarding" replace />;
  }
  return <Outlet />;
}

export function RequireDev(): JSX.Element {
  const me = useMe();

  if (me.isLoading || me.data === undefined || me.data === null) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        正在验证开发者权限…
      </div>
    );
  }
  if (me.data.user.role !== "dev") {
    return <Navigate to="/me" replace />;
  }
  return <Outlet />;
}
