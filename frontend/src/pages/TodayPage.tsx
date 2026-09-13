import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  CircleDot,
  Clock3,
  RefreshCw,
  Sparkles,
  WifiOff,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useMe } from "@/api/auth";
import { useCancelRun, useRun } from "@/api/agent-runs";
import { useCancelGoalBrief, useConfirmGoalBrief, useCreateGoalBrief, useRefineGoalBrief } from "@/api/goal-briefs";
import { useRunEventStream, type RunStreamState } from "@/api/sse";
import type { AgentRunResponse, GoalBriefResponse, ObjectiveType, TaskResponse } from "@/api/types";
import { TaskCard } from "@/components/TaskCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { toUserFacingError } from "@/lib/errors";
import { GOAL_LABELS, STAGE_LABELS } from "@/lib/labels";

const TERMINAL = new Set(["completed", "degraded", "failed", "cancelled"]);
const QUICK_INTENTS = [
  "从零规划求职准备",
  "做一个能写进简历的项目",
  "开始准备投递",
  "准备下一场面试",
];
const OBJECTIVE_LABELS: Record<ObjectiveType, string> = {
  career_plan: "职业规划",
  project: "项目设计",
  application: "岗位投递",
  interview: "面试准备",
  skill_transition: "技能转型",
};

function displayDate(): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
}

function nextActionTask(tasks: TaskResponse[]): TaskResponse | undefined {
  return tasks.find((task) => task.state === "in_progress") ?? tasks.find((task) => task.state === "pending");
}

export function TodayPage(): JSX.Element {
  const [searchParams] = useSearchParams();
  const me = useMe();
  const createGoalBrief = useCreateGoalBrief();
  const refineGoalBrief = useRefineGoalBrief();
  const confirmGoalBrief = useConfirmGoalBrief();
  const cancelGoalBrief = useCancelGoalBrief();
  const cancelRun = useCancelRun();
  const [message, setMessage] = useState("");
  const [submittedRunId, setSubmittedRunId] = useState<string | null>(() => searchParams.get("run_id"));
  const [localBrief, setLocalBrief] = useState<GoalBriefResponse | null>(null);
  const [dismissedBriefId, setDismissedBriefId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const refreshedPlanId = useRef<string | null>(null);

  const activeRunId = submittedRunId ?? me.data?.active_run?.run_id ?? null;
  const stream = useRunEventStream(activeRunId ?? undefined);
  const runQuery = useRun(activeRunId ?? undefined);
  const run = runQuery.data;
  const restoredBrief = me.data?.active_goal_brief ?? null;
  const activeBrief = localBrief ?? (
    restoredBrief?.goal_brief_id === dismissedBriefId ? null : restoredBrief
  );

  useEffect(() => {
    if (
      run?.final_plan_id !== null
      && run?.final_plan_id !== undefined
      && TERMINAL.has(run.status)
      && refreshedPlanId.current !== run.final_plan_id
    ) {
      refreshedPlanId.current = run.final_plan_id;
      void me.refetch();
    }
  }, [me, run?.final_plan_id, run?.status]);

  if (me.isLoading || me.data === undefined || me.data === null) {
    return <div className="text-sm text-muted-foreground">正在准备今天的行动…</div>;
  }

  const activePlan = me.data.active_plan;
  const tasks = me.data.today_tasks;
  const firstTask = nextActionTask(tasks);
  const orderedTasks = [...tasks].sort((left, right) => {
    const priority: Record<TaskResponse["state"], number> = {
      in_progress: 0,
      pending: 1,
      completed: 2,
      abandoned: 3,
      expired: 4,
    };
    return priority[left.state] - priority[right.state] || left.order_index - right.order_index;
  });
  const allSettled = tasks.length > 0 && tasks.every((task) => ["completed", "abandoned", "expired"].includes(task.state));
  const completedCount = tasks.filter((task) => task.state === "completed").length;
  const totalMinutes = tasks.reduce((sum, task) => sum + task.estimated_minutes, 0);
  const isPlanning = run?.status === "pending" || run?.status === "running";
  const planningWindowValid = me.data.planning_window_valid !== false;

  function submitPlan(event: React.FormEvent): void {
    event.preventDefault();
    const trimmed = message.trim();
    if (trimmed.length === 0 || createGoalBrief.isPending || isPlanning || activeBrief !== null) return;
    createGoalBrief.mutate(
      {
        payload: {
          message: trimmed,
          hint_intent: activePlan === null ? "create_plan" : "replan",
          source_plan_id: activePlan?.plan_id ?? null,
        },
        idempotencyKey: `create-${Date.now()}`,
      },
      {
        onSuccess: (created) => {
          setLocalBrief(created);
          setDismissedBriefId(null);
          setMessage("");
          setFeedback(null);
          void me.refetch();
        },
      },
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 sm:space-y-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-primary">{displayDate()} · 今日计划</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">
            {firstTask !== undefined ? "今天只推进一个关键结果" : allSettled ? "今天已经收好尾了" : "今天先确定一个可执行动作"}
          </h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {me.data.profile === null
              ? "这里专注今天的执行，不展示完整工作台总览。"
              : `${GOAL_LABELS[me.data.profile.goal_type]} · ${STAGE_LABELS[me.data.profile.stage]} · 每天约 ${me.data.profile.time_budget_minutes} 分钟`}
          </p>
        </div>
        <Link to="/workspace" className="inline-flex min-h-11 items-center gap-1 text-sm font-medium text-primary hover:underline">
          回到工作台 <ArrowRight className="h-4 w-4" />
        </Link>
      </header>

      {run !== undefined && (
        <RunPanel
          run={run}
          stream={stream}
          onCancel={() => activeRunId !== null && cancelRun.mutate(activeRunId)}
          onRetry={() => setSubmittedRunId(null)}
        />
      )}

      {activeBrief !== null && !isPlanning && (
        <GoalBriefPanel
          brief={activeBrief}
          startDate={me.data.profile?.start_date ?? null}
          endDate={me.data.profile?.deadline ?? null}
          pending={refineGoalBrief.isPending || confirmGoalBrief.isPending || cancelGoalBrief.isPending}
          onRefine={(refinement) => refineGoalBrief.mutate(
            { briefId: activeBrief.goal_brief_id, version: activeBrief.version, message: refinement },
            { onSuccess: setLocalBrief },
          )}
          onConfirm={() => confirmGoalBrief.mutate(
            { briefId: activeBrief.goal_brief_id, version: activeBrief.version },
            { onSuccess: (result) => { setDismissedBriefId(activeBrief.goal_brief_id); setLocalBrief(null); setSubmittedRunId(result.run.run_id); void me.refetch(); } },
          )}
          onCancel={() => cancelGoalBrief.mutate(
            { briefId: activeBrief.goal_brief_id, version: activeBrief.version },
            { onSuccess: () => { setDismissedBriefId(activeBrief.goal_brief_id); setLocalBrief(null); void me.refetch(); } },
          )}
        />
      )}

      {feedback !== null && (
        <div className="flex items-start gap-3 rounded-2xl border border-primary/20 bg-accent/55 p-4 text-sm leading-6">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
          <div>
            <div className="font-medium">搭子反馈</div>
            <p className="text-muted-foreground">{feedback}</p>
          </div>
        </div>
      )}

      {!isPlanning && activeBrief === null && activePlan === null && planningWindowValid && (
        <CreatePlanPanel
          message={message}
          onMessageChange={setMessage}
          onSubmit={submitPlan}
          pending={createGoalBrief.isPending}
          error={createGoalBrief.error}
        />
      )}

      {!isPlanning && activeBrief === null && activePlan === null && !planningWindowValid && (
        <Card className="border-amber-300/60 bg-amber-50/70"><CardContent className="flex flex-wrap items-center justify-between gap-3 p-5"><div><p className="font-medium">规划周期已经结束</p><p className="mt-1 text-sm text-muted-foreground">面试、报告和材料仍可正常使用；更新日期后可创建新路线。</p></div><Button asChild variant="outline"><Link to="/settings/profile">更新规划日期</Link></Button></CardContent></Card>
      )}

      {!isPlanning && activeBrief === null && activePlan !== null && tasks.length > 0 && (
        <section className="space-y-4" aria-labelledby="today-tasks-heading">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">今日执行</p>
              <h2 id="today-tasks-heading" className="mt-1 text-lg font-semibold">今天的计划</h2>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="secondary">已完成 {completedCount}/{tasks.length}</Badge>
              <Badge variant="outline" className="gap-1 py-1"><Clock3 className="h-3.5 w-3.5" />共 {totalMinutes} 分钟</Badge>
            </div>
          </div>
          <p className="text-sm leading-6 text-muted-foreground">逐项完成执行步骤，达到验收标准后再完成任务；点击任务标题可查看细节并调整。</p>
          <div className="space-y-3">
            {orderedTasks.map((task) => (
              <TaskCard key={task.task_id} task={task} featured={task.task_id === firstTask?.task_id} onFeedback={setFeedback} />
            ))}
          </div>
        </section>
      )}

      {allSettled && !isPlanning && activeBrief === null && activePlan !== null && (
        <Card className="border-primary/20 bg-gradient-to-br from-card to-accent/40">
          <CardContent className="space-y-4 p-5 sm:p-6">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-primary-foreground">
              <CheckCircle2 className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-semibold">收下今天的进展</h2>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                完成 {completedCount} 步。用 30 秒记下阻碍，让本周后续安排更贴合真实节奏。
              </p>
            </div>
            <Button asChild><Link to="/reviews">开始今日复盘 <ArrowRight className="h-4 w-4" /></Link></Button>
          </CardContent>
        </Card>
      )}

      {!isPlanning && activeBrief === null && activePlan !== null && tasks.length === 0 && (
        <Card className="border-primary/15">
          <CardHeader>
            <CardDescription>今天只看今天</CardDescription>
            <CardTitle className="text-lg">今天没有需要执行的任务</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm leading-6 text-muted-foreground">
            <p>未来安排保留在路线页，不提前占用今天的注意力。</p>
            <Button asChild variant="outline" size="sm"><Link to={`/journey/${activePlan.plan_id}`}>查看本周路线</Link></Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function GoalBriefPanel({
  brief,
  startDate,
  endDate,
  pending,
  onRefine,
  onConfirm,
  onCancel,
}: {
  brief: GoalBriefResponse;
  startDate: string | null;
  endDate: string | null;
  pending: boolean;
  onRefine: (message: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}): JSX.Element {
  const [refinement, setRefinement] = useState("");
  const needsClarification = brief.status === "clarification_required";
  return (
    <Card className="border-primary/25 bg-gradient-to-br from-accent/45 to-card">
      <CardHeader>
        <CardDescription>{needsClarification ? "再补充一点，避免做错方向" : "执行前请确认目标"}</CardDescription>
        <CardTitle className="text-xl">{brief.objective ?? "待明确的职业目标"}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        {needsClarification && (
          <div className="rounded-xl border border-amber-300/60 bg-amber-50/70 p-4 text-sm">
            <div className="font-medium">还需要你决定</div>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
              {brief.questions.map((question) => <li key={question}>{question}</li>)}
            </ul>
          </div>
        )}
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div><dt className="text-muted-foreground">目标类型</dt><dd className="mt-1 font-medium">{brief.objective_type === null ? "待补充" : OBJECTIVE_LABELS[brief.objective_type]}</dd></div>
          <div><dt className="text-muted-foreground">面向岗位</dt><dd className="mt-1 font-medium">{brief.target_role ?? "待补充"}</dd></div>
          <div><dt className="text-muted-foreground">规划时间</dt><dd className="mt-1 font-medium">{startDate !== null && endDate !== null ? `${startDate} 至 ${endDate}` : "待补充"}</dd></div>
          <div><dt className="text-muted-foreground">能力重点</dt><dd className="mt-1 leading-6">{brief.capability_focus.join("、")}</dd></div>
          <div><dt className="text-muted-foreground">技术栈</dt><dd className="mt-1 leading-6">{brief.tech_stack.join("、")}</dd></div>
        </dl>
        <div className="text-sm">
          <div className="text-muted-foreground">预期交付物</div>
          <p className="mt-1 leading-6">{brief.deliverables.join("、")}</p>
        </div>
        {brief.assumptions.length > 0 && (
          <div className="rounded-xl bg-muted/60 p-3 text-xs leading-5 text-muted-foreground">
            系统建议：{brief.assumptions.join("；")}。你可以在下方修改。
          </div>
        )}
        <div className="rounded-xl border border-primary/15 bg-background/70 p-3 text-sm leading-6">
          确认后会严格在上述日期内生成 {brief.duration_weeks ?? "1–8"} 个周期重点，并只展开首个固定周期的每日任务；提前完成不会滚动补位，也不会生成未来周期。
        </div>
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (refinement.trim()) onRefine(refinement.trim());
          }}
        >
          <Textarea value={refinement} onChange={(event) => setRefinement(event.target.value)} maxLength={2000} rows={3} placeholder="补充或修改，例如：使用 Python + FastAPI，重点展示评测与可观测性" />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="outline" disabled={pending || refinement.trim().length === 0}>更新目标</Button>
            {!needsClarification && <Button type="button" disabled={pending} onClick={onConfirm}>确认并开始生成</Button>}
            <Button type="button" variant="ghost" disabled={pending} onClick={onCancel}>取消草案</Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function CreatePlanPanel({
  message,
  onMessageChange,
  onSubmit,
  pending,
  error,
}: {
  message: string;
  onMessageChange: (value: string) => void;
  onSubmit: (event: React.FormEvent) => void;
  pending: boolean;
  error: unknown;
}): JSX.Element {
  const displayError = error === null ? null : toUserFacingError(error);
  return (
    <Card className="overflow-hidden border-primary/20 shadow-[0_18px_60px_-36px_rgba(24,122,112,0.5)]">
      <CardHeader className="bg-gradient-to-br from-accent/60 to-card pb-5">
        <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-xl bg-primary text-primary-foreground">
          <Sparkles className="h-5 w-5" />
        </div>
        <CardTitle>今天想解决什么？</CardTitle>
        <CardDescription className="leading-6">先选一个常见目标，也可以直接说出你当下最需要解决的问题。</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 p-5 sm:p-6">
        <div className="flex flex-wrap gap-2">
          {QUICK_INTENTS.map((intent) => (
            <Button key={intent} type="button" variant={message === intent ? "default" : "outline"} size="sm" onClick={() => onMessageChange(intent)}>
              {intent}
            </Button>
          ))}
        </div>
        <form onSubmit={onSubmit} className="space-y-3">
          <Textarea rows={4} value={message} onChange={(event) => onMessageChange(event.target.value)} maxLength={2000} placeholder="例如：两周内做出一个能写进简历的 Agent 项目" />
          {displayError !== null && (
            <div className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive">
              <div className="font-medium">{displayError.title}</div>
              <div className="mt-1">{displayError.message}</div>
            </div>
          )}
          <Button type="submit" className="w-full sm:w-auto" disabled={pending || message.trim().length === 0}>
            {pending ? "正在提交…" : "为我生成路线"}<ArrowRight className="h-4 w-4" />
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function RunPanel({
  run,
  stream,
  onCancel,
  onRetry,
}: {
  run: AgentRunResponse;
  stream: RunStreamState;
  onCancel: () => void;
  onRetry: () => void;
}): JSX.Element | null {
  if (run.status === "pending" || run.status === "running") {
    const isStopping = run.user_status === "stopping";
    const progress = isStopping
      ? run.status_message
      : stream.progressMessage ?? run.status_message;
    return (
      <Card className="border-primary/25 bg-accent/35">
        <CardContent className="space-y-4 p-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 gap-3">
              <span className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                <RefreshCw className="h-4 w-4 animate-spin" />
              </span>
              <div>
                <div className="font-medium">{isStopping ? "正在停止" : "正在为你生成路线"}</div>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">{progress}</p>
              </div>
            </div>
            <Button variant="ghost" size="sm" disabled={isStopping} onClick={onCancel}>
              {isStopping ? "正在停止…" : "取消"}
            </Button>
          </div>
          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
            <span className="flex items-center gap-1.5"><CheckCircle2 className="h-3.5 w-3.5 text-primary" />理解目标与限制</span>
            <span className="flex items-center gap-1.5"><CircleDot className="h-3.5 w-3.5 animate-pulse text-primary" />整理行动路径</span>
            <span className="flex items-center gap-1.5"><CircleDot className="h-3.5 w-3.5" />检查今日任务</span>
          </div>
          {stream.connectionState === "reconnecting" && (
            <p className="flex items-center gap-2 rounded-xl bg-background/70 p-3 text-xs text-muted-foreground">
              <WifiOff className="h-4 w-4" />连接中断，正在恢复；你的进度不会丢失。
            </p>
          )}
        </CardContent>
      </Card>
    );
  }

  if (run.result_kind === "clarification" && run.result !== null && "questions" in run.result) {
    return (
      <Card className="border-amber-300/60 bg-amber-50/60">
        <CardContent className="space-y-3 p-5 text-sm">
          <div className="font-medium">还需要补充一点信息</div>
          <p className="text-muted-foreground">{run.result.message}</p>
          <ul className="list-disc space-y-1 pl-5 text-muted-foreground">{run.result.questions.map((question) => <li key={question}>{question}</li>)}</ul>
          <div className="flex flex-wrap gap-2">
            {run.result.suggested_actions.map((action) => (
              <Button key={action.action} asChild variant="outline" size="sm">
                <Link to={action.target_route}>{action.label}</Link>
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }
  if (
    run.result_kind === "navigation"
    && run.result !== null
    && "target_route" in run.result
    && "label" in run.result
  ) {
    return (
      <Card className="border-primary/25 bg-accent/35">
        <CardContent className="space-y-3 p-5 text-sm">
          <div className="font-medium">可以直接为你打开</div>
          <p className="text-muted-foreground">{run.result.message}</p>
          <Button asChild size="sm"><Link to={run.result.target_route}>{run.result.label}</Link></Button>
        </CardContent>
      </Card>
    );
  }
  if (run.result_kind === "safe_response" && run.result !== null && "message" in run.result) {
    return <Card><CardContent className="p-5 text-sm leading-6">{run.result.message}</CardContent></Card>;
  }
  if (run.status === "failed") {
    return (
      <Card className="border-destructive/25">
        <CardContent className="space-y-3 p-5">
          <div className="font-medium">暂时没能完成这份路线</div>
          <p className="text-sm text-muted-foreground">稍后重试即可，已经保存的画像和任务不会丢失。</p>
          <Button variant="outline" size="sm" onClick={onRetry}><RefreshCw className="h-4 w-4" />重新尝试</Button>
        </CardContent>
      </Card>
    );
  }
  if (run.status === "cancelled") {
    return <div className="rounded-xl bg-muted px-4 py-3 text-sm text-muted-foreground">本次生成已取消。</div>;
  }
  if (TERMINAL.has(run.status) && run.result_kind === "plan") {
    return null;
  }
  return null;
}
