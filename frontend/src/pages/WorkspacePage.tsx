import {
  ArrowRight,
  BrainCircuit,
  CalendarCheck2,
  CheckCircle2,
  CircleDot,
  FileText,
  Lightbulb,
  Map,
  MessageSquareText,
  Sparkles,
} from "lucide-react";
import { Link } from "react-router-dom";

import { useMe } from "@/api/auth";
import { useInterviews } from "@/api/interviews";
import { useJobTargets, useResumeAssessments, useResumeVersions } from "@/api/resumes";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { isInterviewReportSeen } from "@/lib/interview-report";
import { GOAL_LABELS, STAGE_LABELS } from "@/lib/labels";

type WorkspaceRecommendation = {
  title: string;
  reason: string;
  actionLabel: string;
  actionTo: string;
  outcome: string;
};

function displayDate(): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());
}

export function WorkspacePage(): JSX.Element {
  const me = useMe();
  const interviews = useInterviews();
  const resumes = useResumeVersions();
  const targets = useJobTargets();
  const assessments = useResumeAssessments();

  if (me.isLoading || me.data === undefined || me.data === null) {
    return <div className="text-sm text-muted-foreground">正在整理你的求职工作台…</div>;
  }

  const activePlan = me.data.active_plan;
  const tasks = me.data.today_tasks;
  const activeRun = me.data.active_run;
  const activeBrief = me.data.active_goal_brief;
  const resumeCount = resumes.data?.items.length ?? 0;
  const targetCount = targets.data?.items.length ?? 0;
  const interviewItems = interviews.data?.items ?? [];
  const interviewCount = interviewItems.length;
  const completedCount = tasks.filter((task) => task.state === "completed").length;
  const nextTask = tasks.find((task) => task.state === "in_progress") ?? tasks.find((task) => task.state === "pending");
  const unfinishedInterview = interviewItems.find((item) => !["completed", "aborted"].includes(item.status));
  const readyReport = interviewItems.find((item) => item.report_status === "ready" && !isInterviewReportSeen(item.interview_id));
  const materialReady = resumeCount > 0 && targetCount > 0;
  const latestAssessment = assessments.data?.[0];
  const decidedClaimIds = new Set(latestAssessment?.rewrite_decisions.map((item) => item.claim_id) ?? []);
  const pendingRewriteCount = latestAssessment?.claims.filter((item) => item.suggested_rewrite && !decidedClaimIds.has(item.claim_id)).length ?? 0;
  const profileLabel = me.data.profile === null
    ? "画像待完善"
    : `${GOAL_LABELS[me.data.profile.goal_type]} · ${STAGE_LABELS[me.data.profile.stage]} · 每天约 ${me.data.profile.time_budget_minutes} 分钟`;
  const companionStatus = activeRun !== null
    ? "处理中"
    : activeBrief !== null
      ? "待确认"
      : activePlan !== null
        ? "已接管"
        : "待启动";

  const recommendation: WorkspaceRecommendation = unfinishedInterview
    ? {
      title: `继续第 ${Math.max(unfinishedInterview.asked_question_count, 1)} 题面试`,
      reason: "你有一场还没结束的面试。先恢复现场，能避免回答上下文断掉。",
      actionLabel: "继续面试",
      actionTo: `/interviews/${unfinishedInterview.interview_id}`,
      outcome: "保留这一轮回答与追问记录，后续可生成完整面试报告。",
    }
    : pendingRewriteCount > 0
      ? {
        title: `确认 ${pendingRewriteCount} 条简历优化建议`,
        reason: "搭子已结合目标 JD 和面试原回答完成核验，需要你决定哪些建议进入新版本。",
        actionLabel: "处理优化建议",
        actionTo: "/materials",
        outcome: "保留原简历，并把确认后的内容生成可回溯的新版本。",
      }
    : readyReport
      ? {
        title: "处理刚生成的面试报告",
        reason: "报告已经就绪。现在最值得做的是把薄弱点转成下一轮训练动作。",
        actionLabel: "查看报告",
        actionTo: `/interviews/${readyReport.interview_id}/report`,
        outcome: "把报告中的问题沉淀为可跟踪训练项，而不是只停留在诊断。",
      }
      : activeBrief !== null
        ? {
          title: "确认搭子整理好的目标",
          reason: "开始生成路线前，需要先确认目标、岗位方向和交付物，避免后续计划跑偏。",
          actionLabel: "去确认目标",
          actionTo: "/today",
          outcome: "确认后会生成路线，并把近期目标拆成今日可执行任务。",
        }
        : activeRun !== null
          ? {
            title: "等待搭子完成处理",
            reason: activeRun.status_message,
            actionLabel: "查看进度",
            actionTo: `/today?run_id=${activeRun.run_id}`,
            outcome: "处理完成后，工作台会更新路线、任务或下一步建议。",
          }
          : nextTask !== undefined
            ? {
              title: nextTask.title,
              reason: "这一步来自当前求职路线，是今天最值得推进的动作。",
              actionLabel: "进入今日计划",
              actionTo: "/today",
              outcome: "完成后可以通过复盘把真实进展反馈给后续安排。",
            }
            : activePlan !== null
              ? {
                title: "检查路线并安排下一步",
                reason: "当前没有未完成的今日任务，可以查看路线或通过复盘推动后续调整。",
                actionLabel: "查看路线",
                actionTo: `/journey/${activePlan.plan_id}`,
                outcome: "确认下一阶段重点，避免准备过程断档。",
              }
              : materialReady
                ? {
                  title: "生成一条求职准备路线",
                  reason: "材料已经有了基础上下文，下一步适合让搭子把目标拆成路线和今日行动。",
                  actionLabel: "生成路线",
                  actionTo: "/today",
                  outcome: "形成可执行计划，让材料、项目和面试准备串起来。",
                }
                : {
                  title: "先补齐简历和目标 JD",
                  reason: "简历和 JD 是后续面试追问、报告分析和改写建议的基础上下文。",
                  actionLabel: "完善求职材料",
                  actionTo: "/materials",
                  outcome: "形成可复用材料上下文，后续训练会围绕这些内容展开。",
                };

  const contextItems = [
    me.data.profile !== null ? `${GOAL_LABELS[me.data.profile.goal_type]}画像` : null,
    materialReady ? "材料已准备" : "材料待补齐",
    activePlan !== null ? "路线已生成" : null,
    tasks.length > 0 ? `${completedCount}/${tasks.length} 今日任务` : null,
    readyReport !== undefined ? "报告待处理" : null,
    pendingRewriteCount > 0 ? `${pendingRewriteCount} 条改写待确认` : null,
  ].filter((item): item is string => item !== null);

  const overviewCards = [
    {
      title: "求职材料",
      description: materialReady
        ? pendingRewriteCount > 0 ? `${pendingRewriteCount} 条基于面试证据的建议待确认。` : `已有 ${resumeCount} 份简历版本、${targetCount} 个目标 JD。`
        : "先准备简历和目标 JD，让后续训练有依据。",
      icon: FileText,
      status: pendingRewriteCount > 0 ? "待确认" : materialReady ? "已准备" : "待补齐",
      actionLabel: pendingRewriteCount > 0 ? "处理建议" : materialReady ? "查看材料" : "完善材料",
      to: "/materials",
    },
    {
      title: "路线",
      description: activePlan !== null
        ? "当前路线已生成，可以查看阶段重点和任务安排。"
        : "还没有路线，搭子可以根据目标生成准备路径。",
      icon: Map,
      status: activePlan !== null ? "进行中" : "待生成",
      actionLabel: activePlan !== null ? "查看路线" : "生成路线",
      to: activePlan !== null ? `/journey/${activePlan.plan_id}` : "/today",
    },
    {
      title: "今日计划",
      description: tasks.length > 0
        ? `今天 ${tasks.length} 个任务，已完成 ${completedCount} 个。`
        : "今天暂时没有安排，适合检查路线或补齐材料。",
      icon: CalendarCheck2,
      status: tasks.length > 0 ? "可执行" : "暂无任务",
      actionLabel: "进入今日计划",
      to: "/today",
    },
    {
      title: "面试复盘",
      description: readyReport !== undefined
        ? "有一份新报告待处理。"
        : interviewCount > 0
          ? `已有 ${interviewCount} 场面试记录。`
          : "可以基于简历和 JD 开始结构化面试。",
      icon: MessageSquareText,
      status: readyReport !== undefined ? "报告待处理" : unfinishedInterview !== undefined ? "进行中" : "可开始",
      actionLabel: unfinishedInterview !== undefined ? "继续面试" : readyReport !== undefined ? "查看报告" : "开始面试",
      to: unfinishedInterview !== undefined
        ? `/interviews/${unfinishedInterview.interview_id}`
        : readyReport !== undefined
          ? `/interviews/${readyReport.interview_id}/report`
          : "/interviews/new",
    },
  ];

  const loopItems = [
    { label: "材料", detail: `${resumeCount} 简历 / ${targetCount} JD`, ready: materialReady },
    { label: "路线", detail: activePlan !== null ? "已生成" : "待生成", ready: activePlan !== null },
    { label: "执行", detail: tasks.length > 0 ? `${completedCount}/${tasks.length} 已完成` : "暂无今日任务", ready: tasks.length > 0 },
    { label: "复盘", detail: readyReport !== undefined ? "报告待处理" : interviewCount > 0 ? `${interviewCount} 场记录` : "未开始", ready: interviewCount > 0 },
  ];

  return (
    <div className="mx-auto max-w-6xl space-y-6 sm:space-y-8">
      <section className="overflow-hidden rounded-[2rem] border border-primary/15 bg-[radial-gradient(circle_at_top_left,hsl(var(--accent)),transparent_34rem),linear-gradient(135deg,hsl(var(--card)),hsl(var(--background)))] p-5 shadow-[0_24px_80px_-56px_rgba(24,122,112,0.65)] sm:p-7">
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)] lg:items-end">
          <div>
            <p className="text-sm font-medium text-primary">{displayDate()} · 求职搭子工作台</p>
            <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">
              让每一次准备都有下一步
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">
              工作台只看全局状态和搭子的下一步建议；具体任务执行放在今日计划里，避免把行动页和总览页混在一起。
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Badge variant="secondary">{profileLabel}</Badge>
              {contextItems.map((item) => <Badge key={item} variant="outline">{item}</Badge>)}
              <Badge variant="secondary">搭子状态：{companionStatus}</Badge>
            </div>
            <div className="mt-6 flex flex-wrap gap-3">
              <Button asChild size="lg"><Link to={recommendation.actionTo}>{recommendation.actionLabel}<ArrowRight className="h-4 w-4" /></Link></Button>
              <Button asChild size="lg" variant="outline"><Link to="/today">进入今日计划</Link></Button>
              <Button asChild size="lg" variant="ghost"><Link to="/materials">完善求职材料</Link></Button>
            </div>
          </div>
          <Card className="border-primary/20 bg-card/85 shadow-none backdrop-blur">
            <CardHeader>
              <CardDescription>搭子当前建议</CardDescription>
              <CardTitle className="text-xl">{recommendation.title}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm leading-6 text-muted-foreground">{recommendation.reason}</p>
              <div className="grid grid-cols-3 gap-2 text-center text-xs">
                <div className="rounded-2xl bg-accent/55 p-3"><div className="text-lg font-semibold text-primary">{resumeCount}</div><div className="text-muted-foreground">简历版本</div></div>
                <div className="rounded-2xl bg-accent/55 p-3"><div className="text-lg font-semibold text-primary">{targetCount}</div><div className="text-muted-foreground">目标 JD</div></div>
                <div className="rounded-2xl bg-accent/55 p-3"><div className="text-lg font-semibold text-primary">{interviewCount}</div><div className="text-muted-foreground">面试记录</div></div>
              </div>
            </CardContent>
          </Card>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4" aria-label="求职准备总览">
        {overviewCards.map((card) => {
          const Icon = card.icon;
          return (
            <Card key={card.title} className="border-primary/15 bg-card/95">
              <CardHeader className="space-y-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                    <Icon className="h-5 w-5" />
                  </span>
                  <Badge variant="secondary">{card.status}</Badge>
                </div>
                <div>
                  <CardTitle className="text-lg">{card.title}</CardTitle>
                  <CardDescription className="mt-2 leading-6">{card.description}</CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <Button asChild variant="outline" className="w-full"><Link to={card.to}>{card.actionLabel}</Link></Button>
              </CardContent>
            </Card>
          );
        })}
      </section>

      <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="border-primary/15">
          <CardHeader>
            <CardDescription>你的求职推进闭环</CardDescription>
            <CardTitle className="text-xl">材料、路线、执行、复盘各司其职</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-4">
            {loopItems.map((item, index) => (
              <div key={item.label} className="relative rounded-2xl border bg-background/70 p-4">
                <div className="flex items-center gap-2">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">{index + 1}</span>
                  <span className="font-medium">{item.label}</span>
                </div>
                <p className="mt-3 text-sm text-muted-foreground">{item.detail}</p>
                <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                  {item.ready ? <CheckCircle2 className="h-3.5 w-3.5 text-primary" /> : <CircleDot className="h-3.5 w-3.5" />}
                  {item.ready ? "已经准备好" : "建议下一步补齐"}
                </p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card className="border-primary/15 bg-card/95">
          <CardHeader>
            <CardDescription>为什么推荐这一步</CardDescription>
            <CardTitle className="text-lg">{recommendation.title}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm leading-6">
            <div className="rounded-2xl bg-accent/45 p-3">
              <div className="flex items-center gap-2 font-medium"><BrainCircuit className="h-4 w-4 text-primary" />参考了哪些信息</div>
              <p className="mt-1 text-muted-foreground">
                {(contextItems.length > 0 ? contextItems : ["你的目标输入"]).join("、")}。
              </p>
            </div>
            <div className="rounded-2xl bg-accent/45 p-3">
              <div className="flex items-center gap-2 font-medium"><Lightbulb className="h-4 w-4 text-primary" />推荐原因</div>
              <p className="mt-1 text-muted-foreground">{recommendation.reason}</p>
            </div>
            <div className="rounded-2xl bg-accent/45 p-3">
              <div className="flex items-center gap-2 font-medium"><Sparkles className="h-4 w-4 text-primary" />完成后会沉淀什么</div>
              <p className="mt-1 text-muted-foreground">{recommendation.outcome}</p>
            </div>
            <Button asChild className="w-full"><Link to={recommendation.actionTo}>{recommendation.actionLabel}</Link></Button>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
