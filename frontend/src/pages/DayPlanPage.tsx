import { useEffect, useState } from "react";
import { ArrowLeft, Bot, Check, Clock3, LockKeyhole, PencilLine, Undo2, X } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import {
  useConfirmTaskAdjustment,
  useCreateTaskAdjustmentProposal,
  useEditTaskDetails,
  usePlan,
  useRejectTaskAdjustment,
  useTaskDetail,
  useUpdateTask,
} from "@/api/plans";
import type { TaskAdjustmentProposalResponse } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toUserFacingError } from "@/lib/errors";
import { TASK_STATUS_LABELS } from "@/lib/labels";

export function DayPlanPage(): JSX.Element {
  const { planId, date } = useParams<{ planId: string; date: string }>();
  const planQuery = usePlan(planId);
  const taskId = planQuery.data?.tasks.find((item) => item.scheduled_date === date)?.task_id;
  const detailQuery = useTaskDetail(taskId);
  const editTask = useEditTaskDetails();
  const createProposal = useCreateTaskAdjustmentProposal();
  const confirmProposal = useConfirmTaskAdjustment();
  const rejectProposal = useRejectTaskAdjustment();
  const updateTask = useUpdateTask();
  const [title, setTitle] = useState("");
  const [starterAction, setStarterAction] = useState("");
  const [deliverable, setDeliverable] = useState("");
  const [rationale, setRationale] = useState("");
  const [minutes, setMinutes] = useState(30);
  const [aiMessage, setAiMessage] = useState("");
  const [proposal, setProposal] = useState<TaskAdjustmentProposalResponse | null>(null);
  const task = detailQuery.data?.task;

  useEffect(() => {
    if (task === undefined) return;
    setTitle(task.title);
    setStarterAction(task.starter_action);
    setDeliverable(task.deliverable);
    setRationale(task.rationale ?? "");
    setMinutes(task.estimated_minutes);
  }, [task]);

  if (planQuery.isLoading || detailQuery.isLoading) {
    return <div className="text-sm text-muted-foreground">正在加载当天计划…</div>;
  }
  if (
    planId === undefined
    || date === undefined
    || planQuery.data === undefined
    || task === undefined
    || detailQuery.data === undefined
  ) {
    return <Card><CardContent className="p-8 text-center text-sm text-muted-foreground">当天计划不存在。</CardContent></Card>;
  }

  const detail = detailQuery.data;
  const currentTask = task;
  const error = updateTask.error ?? editTask.error ?? createProposal.error ?? confirmProposal.error ?? rejectProposal.error;
  const displayError = error === null ? null : toUserFacingError(error);
  const pending = updateTask.isPending || editTask.isPending || createProposal.isPending || confirmProposal.isPending || rejectProposal.isPending;
  const completedLocked = currentTask.state === "completed";

  function saveManual(): void {
    editTask.mutate({
      taskId: currentTask.task_id,
      idempotencyKey: `task-manual-${crypto.randomUUID()}`,
      payload: {
        version: currentTask.version,
        title: title.trim(),
        starter_action: starterAction.trim(),
        deliverable: deliverable.trim(),
        rationale: rationale.trim() || undefined,
        estimated_minutes: minutes,
      },
    });
  }

  function askAi(): void {
    const message = aiMessage.trim();
    if (!message) return;
    createProposal.mutate(
      {
        taskId: currentTask.task_id,
        version: currentTask.version,
        message,
        idempotencyKey: `task-ai-${crypto.randomUUID()}`,
      },
      { onSuccess: setProposal },
    );
  }

  function reopenTask(): void {
    updateTask.mutate({
      taskId: currentTask.task_id,
      payload: { state: "in_progress", version: currentTask.version },
    });
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link to={`/journey/${planId}`} className="inline-flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" />返回本周计划
      </Link>

      <header>
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{date}</Badge>
          <Badge variant="outline">{TASK_STATUS_LABELS[task.state]}</Badge>
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Clock3 className="h-3.5 w-3.5" />{task.estimated_minutes} 分钟</span>
        </div>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight sm:text-3xl">{task.title}</h1>
      </header>

      {completedLocked && (
        <Card className="border-amber-300/70 bg-amber-50/70">
          <CardContent className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <LockKeyhole className="mt-0.5 h-5 w-5 shrink-0 text-amber-800" />
              <div><div className="font-medium text-amber-950">已完成任务暂时锁定</div><p className="mt-1 text-sm leading-6 text-amber-900/80">为避免改写已完成事实，手动修改和 AI 修改已停用。若确实需要调整，请先撤销完成。</p></div>
            </div>
            <Button variant="outline" className="shrink-0 border-amber-400 bg-background font-medium text-amber-950 shadow-sm hover:bg-amber-100" disabled={pending} onClick={reopenTask}><Undo2 className="h-4 w-4" />撤销完成后修改</Button>
          </CardContent>
        </Card>
      )}

      <Card className="border-primary/15 bg-accent/30">
        <CardContent className="space-y-2 p-5 text-sm leading-6">
          <div><span className="font-medium">本周重点：</span>{detail.week_focus}</div>
          <div><span className="font-medium">本周成功标准：</span>{detail.week_success_signal}</div>
          <p className="text-muted-foreground">修改只影响这一天，不会删除完成记录，也不会把本周滚动成新的七天。</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2 text-lg"><PencilLine className="h-4 w-4 text-primary" />手动调整</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          {!detail.editable ? (
            <p className="text-sm text-muted-foreground">{completedLocked ? "任务已完成，请先使用上方“撤销完成后修改”。" : detail.edit_reason ?? "当前任务不可修改。"}</p>
          ) : (
            <>
              <div className="space-y-2"><Label htmlFor="day-title">任务标题</Label><Input id="day-title" value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} /></div>
              <div className="space-y-2"><Label htmlFor="day-starter">如何开始</Label><Textarea id="day-starter" value={starterAction} maxLength={240} onChange={(event) => setStarterAction(event.target.value)} /></div>
              <div className="space-y-2"><Label htmlFor="day-deliverable">完成标志</Label><Textarea id="day-deliverable" value={deliverable} maxLength={240} onChange={(event) => setDeliverable(event.target.value)} /></div>
              <div className="space-y-2"><Label htmlFor="day-rationale">安排原因</Label><Textarea id="day-rationale" value={rationale} maxLength={500} onChange={(event) => setRationale(event.target.value)} /></div>
              <div className="space-y-2"><Label htmlFor="day-minutes">预计分钟数</Label><Input id="day-minutes" type="number" min={5} max={480} value={minutes} onChange={(event) => setMinutes(Number(event.target.value))} /></div>
              <Button disabled={pending || !title.trim() || !starterAction.trim() || !deliverable.trim()} onClick={saveManual}>保存当天调整</Button>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2 text-lg"><Bot className="h-4 w-4 text-primary" />和 AI 商量怎么改</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm leading-6 text-muted-foreground">描述哪里不合理。AI 只会生成修改提案，确认前不会改变任务。</p>
          {!detail.editable && <div className="rounded-xl bg-muted/60 p-3 text-sm text-muted-foreground">{completedLocked ? "任务已完成，撤销完成后才能生成 AI 修改提案。" : "当前任务状态不支持 AI 修改。"}</div>}
          <Textarea value={aiMessage} onChange={(event) => setAiMessage(event.target.value)} maxLength={1000} rows={3} placeholder="例如：这个任务太难了，我今天只有 30 分钟，请拆成一个可验证的小步骤" disabled={!detail.editable} />
          <Button variant="outline" disabled={pending || !detail.editable || !aiMessage.trim()} onClick={askAi}>{createProposal.isPending ? "AI 正在整理…" : "生成调整提案"}</Button>

          {proposal !== null && proposal.status === "pending" && (
            <div className="space-y-3 rounded-2xl border border-primary/20 bg-accent/35 p-4">
              <div><div className="font-medium">AI 调整提案</div><p className="mt-1 text-sm leading-6 text-muted-foreground">{proposal.rationale}</p></div>
              <dl className="grid gap-2 text-sm">
                {Object.entries(proposal.proposed_patch).map(([key, value]) => (
                  <div key={key} className="grid gap-1 rounded-xl bg-background/70 p-3 sm:grid-cols-[120px_1fr]"><dt className="text-muted-foreground">{fieldLabel(key)}</dt><dd>{String(value)}</dd></div>
                ))}
              </dl>
              <div className="flex flex-wrap gap-2">
                <Button disabled={pending} onClick={() => confirmProposal.mutate(
                  { adjustmentId: proposal.adjustment_id, version: proposal.version },
                  { onSuccess: () => { setProposal(null); setAiMessage(""); } },
                )}><Check className="h-4 w-4" />确认应用</Button>
                <Button variant="ghost" disabled={pending} onClick={() => rejectProposal.mutate(
                  { adjustmentId: proposal.adjustment_id, version: proposal.version },
                  { onSuccess: (result) => setProposal(result) },
                )}><X className="h-4 w-4" />拒绝</Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {displayError !== null && <div className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive"><div className="font-medium">{displayError.title}</div><div className="mt-1">{displayError.message}</div></div>}
    </div>
  );
}

function fieldLabel(field: string): string {
  const labels: Record<string, string> = {
    title: "任务标题",
    starter_action: "如何开始",
    deliverable: "完成标志",
    rationale: "安排原因",
    estimated_minutes: "预计分钟",
  };
  return labels[field] ?? field;
}
