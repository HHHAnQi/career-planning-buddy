import { useState } from "react";
import { Check, CheckCircle2, Circle, CircleDot, Minus, Target, Undo2, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { useUpdateTask, useUpdateTaskChecklist, useVerifyTask } from "@/api/plans";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  AbandonedReason,
  TaskResponse,
} from "@/api/types";
import { toUserFacingError } from "@/lib/errors";
import { TASK_STATUS_LABELS, TASK_TYPE_LABELS } from "@/lib/labels";

const ABANDONED_REASON_LABEL: Record<AbandonedReason, string> = {
  too_hard: "太难",
  too_easy: "太简单",
  no_time: "没时间",
  lost_interest: "失去兴趣",
  blocked: "被阻塞",
  other: "其他",
};

export function TaskCard({
  task,
  featured = false,
  onFeedback,
}: {
  task: TaskResponse;
  featured?: boolean;
  onFeedback?: (message: string) => void;
}): JSX.Element {
  const updateTask = useUpdateTask();
  const updateChecklist = useUpdateTaskChecklist();
  const verifyTask = useVerifyTask();
  const [abandonOpen, setAbandonOpen] = useState(false);
  const [verificationOpen, setVerificationOpen] = useState(false);
  const [verificationPhase, setVerificationPhase] = useState<"decision" | "time">("decision");
  const [actualMinutes, setActualMinutes] = useState(task.estimated_minutes);
  const [reason, setReason] = useState<AbandonedReason>("too_hard");
  const [reasonText, setReasonText] = useState("");

  const isCompleted = task.state === "completed";
  const isAbandoned = task.state === "abandoned";
  const executionSteps = task.execution_steps;

  function startTask(): void {
    updateTask.mutate(
      {
        taskId: task.task_id,
        payload: { state: "in_progress", version: task.version },
      },
      { onSuccess: (result) => onFeedback?.(result.companion_message) },
    );
  }

  function confirmVerification(): void {
    verifyTask.mutate(
      {
        taskId: task.task_id,
        payload: { passed: true, version: task.version, actual_minutes: actualMinutes },
      },
      {
        onSuccess: (result) => {
          setVerificationOpen(false);
          setVerificationPhase("decision");
          onFeedback?.(result.companion_message);
        },
      }
    );
  }

  function rejectVerification(): void {
    verifyTask.mutate(
      {
        taskId: task.task_id,
        payload: { passed: false, version: task.version },
      },
      {
        onSuccess: (result) => {
          setVerificationOpen(false);
          setVerificationPhase("decision");
          onFeedback?.(result.companion_message);
        },
      },
    );
  }

  function openVerification(): void {
    setVerificationPhase("decision");
    setVerificationOpen(true);
  }

  function confirmAbandon(): void {
    updateTask.mutate(
      {
        taskId: task.task_id,
        payload: {
          state: "abandoned",
          version: task.version,
          abandoned_reason: reason,
          abandoned_reason_text: reasonText.trim() || null,
        },
      },
      {
        onSuccess: (result) => {
          setAbandonOpen(false);
          onFeedback?.(result.companion_message);
        },
      }
    );
  }

  function statusAction(): void {
    if (task.state === "pending") startTask();
    if (task.state === "in_progress") {
      if (task.completion_ready) openVerification();
      else onFeedback?.("请先逐项完成执行步骤，再进行验收。");
    }
  }

  function updateStep(index: number, completed: boolean): void {
    updateChecklist.mutate(
      {
        taskId: task.task_id,
        payload: { version: task.version, step_index: index, step_completed: completed },
      },
      {
        onSuccess: (result) => {
          onFeedback?.(result.companion_message);
        },
      },
    );
  }

  function reopenTask(): void {
    updateTask.mutate(
      {
        taskId: task.task_id,
        payload: { state: "in_progress", version: task.version },
      },
      { onSuccess: (result) => onFeedback?.(result.companion_message) },
    );
  }

  const statusActionLabel = task.state === "pending"
    ? `开始：${task.title}`
    : task.state === "in_progress"
      ? task.completion_ready
        ? `验收：${task.title}`
        : `进行中：${task.title}`
      : task.state === "completed"
        ? `已完成：${task.title}`
        : `${TASK_STATUS_LABELS[task.state]}：${task.title}`;

  return (
    <Card
      className={`${isAbandoned ? "opacity-70" : ""} ${isCompleted ? "border-primary/20 bg-primary/[0.025]" : ""} ${featured ? "overflow-hidden border-primary/25 shadow-[0_22px_70px_-42px_rgba(24,122,112,0.7)]" : ""}`}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start gap-3">
          <button
            type="button"
            aria-label={statusActionLabel}
            title={statusActionLabel}
            onClick={statusAction}
            disabled={updateTask.isPending || updateChecklist.isPending || verifyTask.isPending || !["pending", "in_progress"].includes(task.state)}
            className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
              task.state === "completed"
                ? "border-primary bg-primary text-primary-foreground"
                : task.state === "in_progress"
                  ? "border-primary bg-accent text-primary"
                  : task.state === "pending"
                    ? "border-muted-foreground/45 text-muted-foreground hover:border-primary hover:text-primary"
                    : "border-muted text-muted-foreground"
            }`}
          >
            {task.state === "completed" ? (
              <Check className="h-5 w-5" />
            ) : task.state === "in_progress" ? (
              <CircleDot className="h-5 w-5" />
            ) : task.state === "pending" ? (
              <Circle className="h-5 w-5" />
            ) : (
              <Minus className="h-5 w-5" />
            )}
          </button>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <CardTitle className="min-w-0 text-base">
                <Link
                  to={`/journey/${task.plan_id}/day/${task.scheduled_date}`}
                  className="hover:text-primary hover:underline"
                >
                  {task.title}
                </Link>
                <Badge variant="outline" className="ml-2 text-xs">
                  {TASK_TYPE_LABELS[task.task_type]}
                </Badge>
              </CardTitle>
              <Badge
                variant={
                  task.state === "completed"
                    ? "success"
                    : task.state === "abandoned"
                      ? "destructive"
                      : task.state === "in_progress"
                        ? "default"
                        : "secondary"
                }
              >
                {TASK_STATUS_LABELS[task.state]}
              </Badge>
            </div>
            <CardDescription className="text-xs">
              预计 {task.estimated_minutes} 分钟
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 pt-0">
        <div className={featured ? "rounded-xl bg-accent/45 p-4" : "rounded-xl bg-muted/35 p-4 text-sm"}>
          <span className="block text-xs font-medium text-muted-foreground">执行步骤</span>
          <ul className="mt-2 space-y-1">
            {executionSteps.map((step) => (
              <li key={step.index}>
                <button
                  type="button"
                  aria-label={`${step.completed ? "取消完成" : "完成"}步骤：${step.text}`}
                  aria-pressed={step.completed}
                  onClick={() => updateStep(step.index, !step.completed)}
                  disabled={updateChecklist.isPending || isAbandoned}
                  className="group flex w-full items-start gap-3 rounded-lg px-2 py-2 text-left text-sm font-medium leading-6 transition-colors hover:bg-background/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <span className={`mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded border transition-colors ${step.completed ? "border-primary bg-primary text-primary-foreground" : "border-muted-foreground/45 bg-background group-hover:border-primary"}`} aria-hidden="true">
                    {step.completed && <Check className="h-3 w-3" />}
                  </span>
                  <span className={step.completed ? "text-muted-foreground line-through" : ""}>{step.text}</span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">逐项点击记录执行进度，再次点击可取消。</p>
        </div>
        <button
          type="button"
          aria-label={`${task.verification_status === "passed" ? "验收已通过" : task.completion_ready ? "开始验收" : "等待验收"}：${task.deliverable}`}
          onClick={openVerification}
          disabled={updateChecklist.isPending || verifyTask.isPending || isAbandoned || isCompleted || !task.completion_ready}
          className={`w-full rounded-xl border p-4 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60 ${task.verification_status === "passed" ? "border-emerald-200 bg-emerald-50/70" : task.verification_status === "failed" ? "border-amber-300 bg-amber-50/70 hover:bg-amber-50" : isAbandoned ? "border-destructive/20 bg-destructive/5" : "border-primary/15 bg-background hover:border-primary/35"}`}
        >
          <div className="flex items-start gap-3">
            {task.verification_status === "passed" ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" /> : isAbandoned ? <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" /> : <Target className={`mt-0.5 h-5 w-5 shrink-0 ${task.verification_status === "failed" ? "text-amber-700" : "text-primary"}`} />}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">验收标准</span>
                <Badge variant={task.verification_status === "passed" ? "success" : isAbandoned ? "destructive" : "secondary"}>
                  {task.verification_status === "passed" ? "已通过" : task.verification_status === "failed" ? "未通过" : task.completion_ready ? "可验收" : isAbandoned ? "未达成" : "等待执行"}
                </Badge>
              </div>
              <p className="mt-1 leading-6">{task.deliverable}</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {task.verification_status === "failed"
                  ? "上次验收未通过。继续完善成果后，可再次点击验收。"
                  : task.completion_ready
                    ? "执行步骤已经完成，点击这里核对成果并验收。"
                    : task.verification_status === "passed"
                      ? "成果已达到标准，该项今日任务已经完成。"
                      : "完成全部执行步骤后才能进行验收。"}
              </p>
            </div>
          </div>
        </button>

        {(updateTask.isError || updateChecklist.isError || verifyTask.isError) && (
          <div className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive">
            {toUserFacingError(updateTask.error ?? updateChecklist.error ?? verifyTask.error).message}
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-xs text-muted-foreground">
            {task.actual_minutes !== null
              ? `实际用时 ${task.actual_minutes} 分钟`
              : task.completion_ready
                ? "执行步骤已完成，等待验收"
                : "验收通过后记录实际用时"}
          </div>
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            {task.state === "in_progress" && (
              <Button size="sm" variant="ghost" onClick={() => setAbandonOpen(true)} disabled={updateTask.isPending || updateChecklist.isPending}>今天先放下</Button>
            )}
            {task.state === "completed" && (
              <Button
                size="sm"
                variant="outline"
                className="border-amber-300 bg-amber-50 font-medium text-amber-900 shadow-sm hover:bg-amber-100 hover:text-amber-950"
                onClick={reopenTask}
                disabled={updateTask.isPending || updateChecklist.isPending || verifyTask.isPending}
              >
                <Undo2 className="h-4 w-4" />撤销完成
              </Button>
            )}
          </div>
        </div>

        {/* 验收对话框 */}
        <Dialog
          open={verificationOpen}
          onOpenChange={(open) => {
            setVerificationOpen(open);
            if (!open) setVerificationPhase("decision");
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{verificationPhase === "decision" ? "验收今日任务" : "记录实际用时"}</DialogTitle>
              <DialogDescription>
                {verificationPhase === "decision"
                  ? "请根据验收标准判断成果，而不是仅凭执行步骤是否勾选。"
                  : `成果已经达到标准，请记录完成「${task.title}」实际花费的时间。`}
              </DialogDescription>
            </DialogHeader>
            {verificationPhase === "decision" ? (
              <>
                <div className="rounded-xl border border-primary/20 bg-accent/40 p-4 text-sm">
                  <div className="font-medium">是否已经达到以下标准？</div>
                  <p className="mt-2 leading-6">{task.deliverable}</p>
                </div>
                <DialogFooter className="gap-2 sm:justify-between">
                  <Button variant="outline" onClick={rejectVerification} disabled={verifyTask.isPending}>还未达到</Button>
                  <Button onClick={() => setVerificationPhase("time")} disabled={verifyTask.isPending}>已达到，继续</Button>
                </DialogFooter>
              </>
            ) : (
              <>
                <div className="space-y-2">
                  <Label htmlFor="actual_minutes">实际用时（分钟）</Label>
                  <Input
                    id="actual_minutes"
                    type="number"
                    min={1}
                    max={1440}
                    value={actualMinutes}
                    onChange={(e) => setActualMinutes(Number(e.target.value))}
                  />
                </div>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setVerificationPhase("decision")} disabled={verifyTask.isPending}>返回</Button>
                  <Button onClick={confirmVerification} disabled={verifyTask.isPending || actualMinutes < 1}>确认验收并完成</Button>
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* 放弃对话框 */}
        <Dialog open={abandonOpen} onOpenChange={setAbandonOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>放弃 {task.title}</DialogTitle>
              <DialogDescription>选一个最贴近的真实原因，搭子会据此调整后续计划。</DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              <div className="space-y-2">
                <Label>原因</Label>
                <Select value={reason} onValueChange={(v) => setReason(v as AbandonedReason)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(Object.keys(ABANDONED_REASON_LABEL) as AbandonedReason[]).map((r) => (
                      <SelectItem key={r} value={r}>
                        {ABANDONED_REASON_LABEL[r]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="reason_text">具体说明（{reason === "other" ? "必填" : "可空"}）</Label>
                <Input
                  id="reason_text"
                  value={reasonText}
                  onChange={(e) => setReasonText(e.target.value)}
                  placeholder="如：信息检索失败、依赖卡住…"
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={confirmAbandon} disabled={updateTask.isPending || (reason === "other" && reasonText.trim().length === 0)}>
                今天先放下
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
