import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Sparkles } from "lucide-react";
import { Navigate, useNavigate } from "react-router-dom";
import { useMe } from "@/api/auth";
import { usePutProfile } from "@/api/profile";
import type { CareerStage, GoalType, ProfilePutRequest, SkillLevel } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toUserFacingError } from "@/lib/errors";
import { GOAL_LABELS, SKILL_LABELS, STAGE_LABELS } from "@/lib/labels";

const TIME_OPTIONS = [30, 45, 60, 90, 120];

function dateInputValue(offsetDays: number): string {
  const value = new Date();
  value.setDate(value.getDate() + offsetDays);
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addDays(value: string, offsetDays: number): string {
  const date = new Date(`${value}T00:00:00`);
  date.setDate(date.getDate() + offsetDays);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function OnboardingPage(): JSX.Element {
  const me = useMe();
  const putProfile = usePutProfile();
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [goalType, setGoalType] = useState<GoalType>("agent_app");
  const [stage, setStage] = useState<CareerStage>("preparing");
  const [timeBudget, setTimeBudget] = useState(60);
  const [skillLevel, setSkillLevel] = useState<SkillLevel>("intermediate");
  const [skillSummary, setSkillSummary] = useState("");
  const [startDate, setStartDate] = useState("");
  const [deadline, setDeadline] = useState("");

  useEffect(() => {
    const existing = me.data?.profile;
    if (existing === null || existing === undefined) return;
    setGoalType(existing.goal_type);
    setStage(existing.stage);
    setTimeBudget(existing.time_budget_minutes);
    setSkillLevel(existing.skill_level);
    setSkillSummary(existing.skill_summary ?? "");
    setStartDate(existing.start_date ?? "");
    setDeadline(existing.deadline ?? "");
  }, [me.data?.profile]);

  if (me.data === undefined || me.data === null) return <div className="flex min-h-screen items-center justify-center text-muted-foreground">正在准备建档…</div>;
  if (me.data.profile_complete) return <Navigate to="/workspace" replace />;

  const error = putProfile.error === null ? null : toUserFacingError(putProfile.error);

  function submit(): void {
    if (putProfile.isPending || startDate.length === 0 || deadline.length === 0) return;
    const payload: ProfilePutRequest = {
      goal_type: goalType,
      stage,
      time_budget_minutes: timeBudget,
      skill_level: skillLevel,
      skill_summary: skillSummary.trim() || null,
      start_date: startDate,
      deadline,
      preferences: {},
    };
    putProfile.mutate(
      { payload, idempotencyKey: `profile-${Date.now()}` },
      { onSuccess: () => navigate("/today") },
    );
  }

  return (
    <div className="min-h-screen bg-background px-4 py-6 sm:px-6 sm:py-10">
      <div className="mx-auto max-w-2xl">
        <div className="mb-8 flex items-center gap-2"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground"><Sparkles className="h-5 w-5" /></span><div><div className="font-semibold">求职搭子</div><div className="text-xs text-muted-foreground">用 1 分钟了解你的目标</div></div></div>

        <div className="mb-6 grid grid-cols-3 gap-2" aria-label={`建档进度：第 ${step} 步，共 3 步`}>
          {[1, 2, 3].map((item) => <div key={item} className={`h-1.5 rounded-full ${item <= step ? "bg-primary" : "bg-secondary"}`} />)}
        </div>

        <Card className="overflow-hidden border-primary/15 shadow-[0_28px_80px_-50px_rgba(24,122,112,0.7)]">
          <CardContent className="p-5 sm:p-8">
            {step === 1 && (
              <div className="space-y-6">
                <header><p className="text-sm font-medium text-primary">第 1 步 · 目标</p><h1 className="mt-1 text-2xl font-semibold tracking-tight">你现在想走向哪里？</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">先确定方向与阶段，具体背景可以稍后补充。</p></header>
                <div className="space-y-2"><Label htmlFor="onboarding-goal">目标岗位类型</Label><Select value={goalType} onValueChange={(value) => setGoalType(value as GoalType)}><SelectTrigger id="onboarding-goal"><SelectValue /></SelectTrigger><SelectContent>{(Object.keys(GOAL_LABELS) as GoalType[]).map((value) => <SelectItem key={value} value={value}>{GOAL_LABELS[value]}</SelectItem>)}</SelectContent></Select></div>
                <div className="space-y-2"><Label>当前阶段</Label><div className="grid gap-2 sm:grid-cols-2">{(Object.keys(STAGE_LABELS) as CareerStage[]).map((value) => <button key={value} type="button" onClick={() => setStage(value)} className={`flex min-h-12 items-center justify-between rounded-xl border px-4 text-left text-sm transition-colors ${stage === value ? "border-primary bg-accent text-accent-foreground" : "bg-card hover:bg-accent/40"}`}><span>{STAGE_LABELS[value]}</span>{stage === value && <Check className="h-4 w-4 text-primary" />}</button>)}</div></div>
              </div>
            )}

            {step === 2 && (
              <div className="space-y-6">
                <header><p className="text-sm font-medium text-primary">第 2 步 · 节奏</p><h1 className="mt-1 text-2xl font-semibold tracking-tight">每天留多少时间更真实？</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">任务总时长不会超过这个预算，少一点也没关系。</p></header>
                <div className="space-y-3"><Label>每天可投入时间</Label><div className="flex flex-wrap gap-2">{TIME_OPTIONS.map((minutes) => <Button key={minutes} type="button" size="sm" variant={timeBudget === minutes ? "default" : "outline"} onClick={() => setTimeBudget(minutes)}>{minutes} 分钟</Button>)}</div><Input aria-label="自定义每日分钟数" type="number" min={15} max={480} value={timeBudget} onChange={(event) => setTimeBudget(Number(event.target.value))} /></div>
                <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Label htmlFor="onboarding-start-date">开始日期</Label><Input id="onboarding-start-date" type="date" required min={dateInputValue(0)} value={startDate} onChange={(event) => setStartDate(event.target.value)} /></div><div className="space-y-2"><Label htmlFor="onboarding-deadline">结束日期</Label><Input id="onboarding-deadline" type="date" required min={startDate || dateInputValue(0)} max={startDate ? addDays(startDate, 55) : undefined} value={deadline} onChange={(event) => setDeadline(event.target.value)} /></div></div><p className="text-xs text-muted-foreground">请选择最长 8 周的执行时间段，系统不会在开始日前或结束日后安排任务。</p>
              </div>
            )}

            {step === 3 && (
              <div className="space-y-6">
                <header><p className="text-sm font-medium text-primary">第 3 步 · 基础</p><h1 className="mt-1 text-2xl font-semibold tracking-tight">从你已经会的地方开始</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">这部分可以简写，搭子只用它避免给出太难或太简单的任务。</p></header>
                <div className="space-y-2"><Label>当前基础</Label><Select value={skillLevel} onValueChange={(value) => setSkillLevel(value as SkillLevel)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{(Object.keys(SKILL_LABELS) as SkillLevel[]).map((value) => <SelectItem key={value} value={value}>{SKILL_LABELS[value]}</SelectItem>)}</SelectContent></Select></div>
                <div className="space-y-2"><Label htmlFor="onboarding-summary">技能与背景（可跳过）</Label><Textarea id="onboarding-summary" rows={4} placeholder="例如：会 Python 和 FastAPI，做过简单的 RAG Demo" value={skillSummary} onChange={(event) => setSkillSummary(event.target.value)} /></div>
                <div className="rounded-xl bg-accent/45 p-4 text-sm leading-6"><span className="font-medium">即将开始：</span><span className="text-muted-foreground">{GOAL_LABELS[goalType]} · {STAGE_LABELS[stage]} · 每天 {timeBudget} 分钟</span></div>
                {error !== null && <div className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive"><div className="font-medium">{error.title}</div><div className="mt-1">{error.message}</div></div>}
              </div>
            )}

            <div className="mt-8 flex items-center justify-between gap-3 border-t pt-5">
              <Button type="button" variant="ghost" disabled={step === 1 || putProfile.isPending} onClick={() => setStep((value) => Math.max(1, value - 1))}><ArrowLeft className="h-4 w-4" />上一步</Button>
              {step < 3 ? <Button type="button" disabled={step === 2 && (timeBudget < 15 || timeBudget > 480 || startDate.length === 0 || deadline.length === 0 || startDate > deadline)} onClick={() => setStep((value) => Math.min(3, value + 1))}>继续 <ArrowRight className="h-4 w-4" /></Button> : <Button type="button" disabled={putProfile.isPending || startDate.length === 0 || deadline.length === 0 || startDate > deadline} onClick={submit}>{putProfile.isPending ? "保存中…" : "保存并开始"}<ArrowRight className="h-4 w-4" /></Button>}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
