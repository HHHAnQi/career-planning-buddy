import { ArrowRight, CheckCircle2, Circle, Clock3 } from "lucide-react";
import { Link } from "react-router-dom";

import type { TaskResponse, TaskStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { TASK_STATUS_LABELS } from "@/lib/labels";

const SETTLED = new Set<TaskStatus>(["completed", "abandoned", "expired"]);

export function localDateIso(value = new Date()): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addDays(isoDate: string, offset: number): string {
  const parts = isoDate.split("-");
  const year = Number(parts[0]);
  const month = Number(parts[1]);
  const day = Number(parts[2]);
  const value = new Date(year, month - 1, day + offset);
  return localDateIso(value);
}

function dateLabel(isoDate: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    weekday: "short",
  }).format(new Date(`${isoDate}T00:00:00`));
}

function dayIsSettled(tasks: TaskResponse[]): boolean {
  return tasks.length > 0 && tasks.every((task) => SETTLED.has(task.state));
}

export function WeeklyTaskSchedule({
  startDate,
  endDate,
  tasks,
  detailed = true,
}: {
  startDate: string;
  endDate?: string;
  tasks: TaskResponse[];
  detailed?: boolean;
}): JSX.Element {
  const today = localDateIso();
  const finalOffset = endDate === undefined
    ? 6
    : Math.max(0, Math.min(6, Math.round(
      (new Date(`${endDate}T00:00:00`).getTime() - new Date(`${startDate}T00:00:00`).getTime())
      / 86_400_000,
    )));
  const rows = Array.from({ length: finalOffset + 1 }, (_, offset) => {
    const date = addDays(startDate, offset);
    return {
      date,
      tasks: tasks
        .filter((task) => task.scheduled_date === date)
        .sort((left, right) => left.order_index - right.order_index),
    };
  });

  return (
    <Card>
      <CardContent className="p-0">
        <ol className="divide-y">
          {rows.map((row) => {
            const isToday = row.date === today;
            const settled = dayIsSettled(row.tasks);
            return (
              <li
                key={row.date}
                className={`grid gap-3 p-4 sm:grid-cols-[120px_minmax(0,1fr)] sm:p-5 ${isToday ? "bg-accent/45" : ""}`}
              >
                <div className="flex items-center gap-2">
                  <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border ${settled ? "border-primary/30 bg-accent text-primary" : isToday ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground"}`}>
                    {settled ? <CheckCircle2 className="h-4 w-4" /> : isToday ? <Clock3 className="h-4 w-4" /> : <Circle className="h-3.5 w-3.5" />}
                  </span>
                  <div>
                    <div className="text-sm font-medium">{dateLabel(row.date)}</div>
                    {isToday && <Badge className="mt-1" variant="secondary">今天</Badge>}
                  </div>
                </div>

                <div className="min-w-0 space-y-3">
                  {row.tasks.length === 0 ? (
                    <p className="pt-1 text-sm text-muted-foreground">当天暂无安排</p>
                  ) : (
                    row.tasks.map((task) => (
                      <div key={task.task_id} className="min-w-0">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="font-medium leading-6">{task.title}</div>
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">{task.estimated_minutes} 分钟</span>
                            <Badge variant={task.state === "completed" ? "success" : task.state === "in_progress" ? "default" : "secondary"}>
                              {TASK_STATUS_LABELS[task.state]}
                            </Badge>
                          </div>
                        </div>
                        <Link
                          to={`/journey/${task.plan_id}/day/${row.date}`}
                          className="mt-2 inline-flex min-h-9 items-center gap-1 text-sm font-medium text-primary hover:underline"
                        >
                          查看与调整 <ArrowRight className="h-3.5 w-3.5" />
                        </Link>
                        {detailed && (
                          <div className="mt-2 space-y-1 text-sm leading-6 text-muted-foreground">
                            <p><span className="font-medium text-foreground">开始：</span>{task.starter_action}</p>
                            <p><span className="font-medium text-foreground">完成标志：</span>{task.deliverable}</p>
                            {task.rationale && <p><span className="font-medium text-foreground">安排原因：</span>{task.rationale}</p>}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
