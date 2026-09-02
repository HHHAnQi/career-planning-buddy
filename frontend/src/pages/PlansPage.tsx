import { ArrowRight, Check, Circle, Flag, Map, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { useActivePlan, usePlans } from "@/api/plans";
import type { ActivePlanResponse } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PLAN_STATUS_LABELS } from "@/lib/labels";

const DAY_MS = 86_400_000;

function dateAtMidnight(isoDate: string): Date {
  return new Date(`${isoDate}T00:00:00`);
}

function addDays(isoDate: string, days: number): string {
  const value = dateAtMidnight(isoDate);
  value.setDate(value.getDate() + days);
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function earlierDate(left: string, right: string): string {
  return left < right ? left : right;
}

function inclusiveDays(start: string, end: string): number {
  return Math.max(1, Math.round((dateAtMidnight(end).getTime() - dateAtMidnight(start).getTime()) / DAY_MS) + 1);
}

function todayIso(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function currentWeek(plan: ActivePlanResponse, today = todayIso()): number {
  const start = dateAtMidnight(plan.horizon_start);
  const elapsed = Math.floor((dateAtMidnight(today).getTime() - start.getTime()) / DAY_MS);
  return Math.min(Math.max(Math.floor(elapsed / 7) + 1, 1), Math.max(plan.weekly_focus.length, 1));
}

export function executionProgress(plan: ActivePlanResponse, today = todayIso()): { elapsed: number; total: number; percent: number; end: string } {
  const end = earlierDate(addDays(plan.plan_date, 6), plan.horizon_end);
  const total = inclusiveDays(plan.plan_date, end);
  const elapsed = today < plan.plan_date ? 0 : today > end ? total : inclusiveDays(plan.plan_date, today);
  return { elapsed, total, percent: Math.round((elapsed / total) * 100), end };
}

export function PlansPage(): JSX.Element {
  const active = useActivePlan();
  const plans = usePlans();

  if (active.isLoading || plans.isLoading) {
    return <div className="text-sm text-muted-foreground">正在整理你的路线…</div>;
  }

  const plan = active.data;
  const today = todayIso();
  const history = (plans.data?.items ?? []).filter(
    (item) => item.plan_id !== plan?.plan_id && item.plan_date <= today && ["completed", "archived"].includes(item.status),
  );

  if (plan === undefined) {
    return (
      <div className="mx-auto max-w-3xl space-y-6">
        <header>
          <p className="text-sm font-medium text-primary">你的路线</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">先生成第一份求职路线</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">路线看中期方向，行动只展开到今天。</p>
        </header>
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-start gap-4 p-6 sm:p-8">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent text-primary"><Map className="h-6 w-6" /></span>
            <div><h2 className="font-semibold">还没有正在进行的路线</h2><p className="mt-1 text-sm text-muted-foreground">告诉搭子你的目标，它会给出 1~8 周方向和今天的第一步。</p></div>
            <Button asChild><Link to="/today">去生成路线 <ArrowRight className="h-4 w-4" /></Link></Button>
          </CardContent>
        </Card>
        {history.length > 0 && <CompletedPeriodList plans={history} />}
      </div>
    );
  }

  const week = currentWeek(plan, today);
  const currentFocus = plan.weekly_focus.find((item) => item.week_index === week);
  const progress = executionProgress(plan, today);
  const cycleState = progress.elapsed === 0 ? "尚未开始" : progress.elapsed === progress.total ? "本周期已到期" : `第 ${progress.elapsed} 天`;

  return (
    <div className="mx-auto max-w-4xl space-y-7">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium text-primary">你的求职路线</p>
          <Badge variant="outline">{today >= plan.plan_date && today <= progress.end ? "当前周期" : PLAN_STATUS_LABELS[plan.status]}</Badge>
        </div>
        <h1 className="max-w-3xl text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">{plan.overall_direction}</h1>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">{plan.summary}</p>
      </header>

      <Card className="overflow-hidden border-primary/20 bg-gradient-to-br from-card via-card to-accent/45">
        <CardContent className="grid gap-6 p-5 sm:p-6 md:grid-cols-[1fr_220px] md:items-center">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">{cycleState} · 第 {week} 周</p>
            <h2 className="mt-2 text-xl font-semibold">{currentFocus?.focus ?? plan.overall_direction}</h2>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              成功信号：{currentFocus?.success_signal ?? "完成今天可验证的第一步"}
            </p>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs text-muted-foreground"><span>{plan.plan_date}</span><span>{progress.end}</span></div>
            <div className="h-2 overflow-hidden rounded-full bg-secondary"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress.percent}%` }} /></div>
            <p className="text-right text-xs text-muted-foreground">本周期进度 {progress.elapsed}/{progress.total} 天 · {progress.percent}%</p>
          </div>
        </CardContent>
      </Card>

      <section className="space-y-4">
        <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">每周重点</h2><span className="text-sm text-muted-foreground">按周期查看</span></div>
        <Card>
          <CardContent className="p-5 sm:p-6">
            <ol className="space-y-0">
              {history.slice().reverse().map((pastPlan) => {
                const periodEnd = earlierDate(addDays(pastPlan.plan_date, 6), pastPlan.horizon_end);
                const days = inclusiveDays(pastPlan.plan_date, periodEnd);
                const focus = pastPlan.weekly_focus[0];
                return (
                  <li key={pastPlan.plan_id} className="relative grid grid-cols-[32px_minmax(0,1fr)] gap-3 pb-6">
                    <span className="relative z-10 flex h-8 w-8 items-center justify-center rounded-full border border-primary/30 bg-accent text-primary"><Check className="h-4 w-4" /></span>
                    <Link to={`/journey/${pastPlan.plan_id}?week=1`} className="min-w-0 rounded-xl px-2 py-1 transition-colors hover:bg-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">{days === 7 ? "已结束周周期" : `已结束 ${days} 天周期`}</span><Badge variant="secondary">已归档</Badge></div>
                      <p className="mt-1 text-sm leading-6">{focus?.focus ?? pastPlan.overall_direction}</p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">{pastPlan.plan_date} 至 {periodEnd} · 点击查看细节</p>
                    </Link>
                  </li>
                );
              })}
              {plan.weekly_focus.map((item, index) => {
                const periodStart = addDays(plan.horizon_start, (item.week_index - 1) * 7);
                const periodEnd = earlierDate(addDays(periodStart, 6), plan.horizon_end);
                const complete = today > periodEnd;
                const current = today >= periodStart && today <= periodEnd;
                return (
                  <li key={item.week_index} className="relative grid grid-cols-[32px_minmax(0,1fr)] gap-3 pb-6 last:pb-0">
                    {index < plan.weekly_focus.length - 1 && <span className="absolute bottom-0 left-[15px] top-8 w-px bg-border" />}
                    <span className={`relative z-10 flex h-8 w-8 items-center justify-center rounded-full border ${current ? "border-primary bg-primary text-primary-foreground" : complete ? "border-primary/30 bg-accent text-primary" : "bg-card text-muted-foreground"}`}>
                      {complete ? <Check className="h-4 w-4" /> : current ? <Sparkles className="h-4 w-4" /> : <Circle className="h-3 w-3" />}
                    </span>
                    <Link to={`/journey/${plan.plan_id}?week=${item.week_index}`} className="min-w-0 rounded-xl px-2 py-1 transition-colors hover:bg-accent/55 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold">{inclusiveDays(periodStart, periodEnd) === 7 ? `第 ${item.week_index} 周` : `${inclusiveDays(periodStart, periodEnd)} 天收尾周期`}</span>{current && <Badge>当前</Badge>}{today < periodStart && item.week_index === 1 && <Badge variant="secondary">待开始</Badge>}</div>
                      <p className="mt-1 text-sm leading-6">{item.focus}</p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">成功信号：{item.success_signal}</p>
                      <p className="mt-1 text-xs leading-5 text-primary">{periodStart} 至 {periodEnd} · 查看细节</p>
                    </Link>
                  </li>
                );
              })}
            </ol>
          </CardContent>
        </Card>
      </section>

      {plan.adjustment_reason !== null && (
        <Card className="border-amber-300/60 bg-amber-50/60">
          <CardContent className="flex gap-3 p-5 text-sm leading-6"><Flag className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" /><div><div className="font-medium">上次调整</div><p className="text-muted-foreground">{plan.adjustment_reason}</p></div></CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-3">
        <Button asChild><Link to={`/journey/${plan.plan_id}`}>查看每日计划 <ArrowRight className="h-4 w-4" /></Link></Button>
        <Button asChild variant="outline"><Link to="/today">去完成今天</Link></Button>
      </div>
    </div>
  );
}

function CompletedPeriodList({ plans }: { plans: ActivePlanResponse[] }): JSX.Element {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">已结束的周期重点</h2>
      <div className="space-y-3">
        {plans.slice().reverse().map((plan) => {
          const periodEnd = earlierDate(addDays(plan.plan_date, 6), plan.horizon_end);
          const days = inclusiveDays(plan.plan_date, periodEnd);
          return (
            <Link key={plan.plan_id} to={`/journey/${plan.plan_id}?week=1`} className="block min-w-0">
              <Card className="transition-colors hover:border-primary/35">
                <CardHeader className="p-5">
                  <div className="flex flex-wrap items-center gap-2"><CardTitle className="text-base leading-6">{plan.weekly_focus[0]?.focus ?? plan.overall_direction}</CardTitle><Badge variant="secondary">{days === 7 ? "已结束周周期" : `已结束 ${days} 天周期`}</Badge></div>
                  <CardDescription>{plan.plan_date} 至 {periodEnd} · 点击查看细节</CardDescription>
                </CardHeader>
              </Card>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
