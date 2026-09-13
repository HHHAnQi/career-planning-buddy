import { ArrowLeft, ExternalLink, Lightbulb, Link2 } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { usePlan } from "@/api/plans";
import { WeeklyTaskSchedule } from "@/components/WeeklyTaskSchedule";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PLAN_STATUS_LABELS } from "@/lib/labels";

export function PlanDetailPage(): JSX.Element {
  const { planId } = useParams<{ planId: string }>();
  const [searchParams] = useSearchParams();
  const planQuery = usePlan(planId);

  if (planQuery.isLoading) return <div className="text-sm text-muted-foreground">正在加载路线详情…</div>;
  if (planQuery.isError || planQuery.data === undefined) {
    return <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">路线不存在或暂时无法加载。</CardContent></Card>;
  }

  const plan = planQuery.data;
  const requestedWeek = Number(searchParams.get("week") ?? "1");
  const weekIndex = Math.min(Math.max(Number.isFinite(requestedWeek) ? requestedWeek : 1, 1), Math.max(plan.weekly_focus.length, 1));
  const selectedFocus = plan.weekly_focus.find((item) => item.week_index === weekIndex) ?? plan.weekly_focus[0];
  const periodStartDate = new Date(`${plan.horizon_start}T00:00:00`);
  periodStartDate.setDate(periodStartDate.getDate() + (weekIndex - 1) * 7);
  const periodStart = `${periodStartDate.getFullYear()}-${String(periodStartDate.getMonth() + 1).padStart(2, "0")}-${String(periodStartDate.getDate()).padStart(2, "0")}`;
  const periodEndDate = new Date(periodStartDate);
  periodEndDate.setDate(periodEndDate.getDate() + 6);
  const rawPeriodEnd = `${periodEndDate.getFullYear()}-${String(periodEndDate.getMonth() + 1).padStart(2, "0")}-${String(periodEndDate.getDate()).padStart(2, "0")}`;
  const periodEnd = rawPeriodEnd < plan.horizon_end ? rawPeriodEnd : plan.horizon_end;
  const periodTasks = plan.tasks.filter((task) => task.scheduled_date >= periodStart && task.scheduled_date <= periodEnd);
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link to="/journey" className="inline-flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="h-4 w-4" />返回路线</Link>
      <header>
        <div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{PLAN_STATUS_LABELS[plan.status]}</Badge><span className="text-xs text-muted-foreground">{plan.horizon_start} 至 {plan.horizon_end}</span></div>
        <h1 className="mt-3 text-2xl font-semibold leading-tight tracking-tight sm:text-3xl">{plan.overall_direction}</h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">{plan.summary}</p>
      </header>

      <Card className="border-primary/15 bg-accent/30">
        <CardContent className="flex gap-3 p-5 sm:p-6"><Lightbulb className="mt-0.5 h-5 w-5 shrink-0 text-primary" /><div><h2 className="font-semibold">为什么这样安排</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{plan.rationale}</p></div></CardContent>
      </Card>

      {selectedFocus !== undefined && (
        <Card className="border-primary/20">
          <CardContent className="space-y-2 p-5 sm:p-6">
            <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">{periodStart} 至 {periodEnd} 的周期重点</h2><Badge variant="secondary">第 {weekIndex} 个周期</Badge></div>
            <p className="text-sm leading-6">{selectedFocus.focus}</p>
            <p className="text-sm leading-6 text-muted-foreground">成功信号：{selectedFocus.success_signal}</p>
          </CardContent>
        </Card>
      )}

      <section className="space-y-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">{periodStart} 至 {periodEnd}</p>
          <h2 className="mt-1 text-lg font-semibold">本周期每日计划</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{periodTasks.length > 0 ? "每天只安排一个关键结果；任务状态只记录在对应日期。" : "该周期尚未展开到每日任务；前一周期结束并完成复盘后，再由你确认生成。"}</p>
        </div>
        {periodTasks.length > 0 && <WeeklyTaskSchedule startDate={periodStart} endDate={periodEnd} tasks={periodTasks} detailed />}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">安排依据与来源</h2>
        {plan.sources.length === 0 ? <Card><CardContent className="p-6 text-sm text-muted-foreground">这份路线没有使用需要单独展示的外部来源。</CardContent></Card> : (
          <div className="space-y-3">{plan.sources.map((source) => <Card key={source.id}><CardContent className="flex items-start gap-3 p-5"><span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-primary"><Link2 className="h-4 w-4" /></span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium">{source.title ?? source.kind}</span>{!source.available && <Badge variant="secondary">当前不可用</Badge>}</div>{source.snippet && <p className="mt-1 text-xs leading-5 text-muted-foreground">{source.snippet}</p>}{source.url && source.available && <a className="mt-2 inline-flex min-h-9 items-center gap-1 text-xs font-medium text-primary hover:underline" href={source.url} target="_blank" rel="noreferrer">打开来源 <ExternalLink className="h-3.5 w-3.5" /></a>}</div></CardContent></Card>)}</div>
        )}
      </section>
    </div>
  );
}
