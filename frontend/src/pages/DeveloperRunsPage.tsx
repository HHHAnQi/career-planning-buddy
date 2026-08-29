import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Box, Braces, CheckCircle2, Clock3, Database, RotateCcw, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { fetchDevRun, fetchDevRuns, replayDevRun } from "@/api/dev";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STATUS_LABEL: Record<string, string> = {
  pending: "等待执行", running: "执行中", completed: "成功", degraded: "降级完成", failed: "失败", cancelled: "已取消",
};

export function DeveloperRunsPage(): JSX.Element {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const runs = useQuery({ queryKey: ["dev-runs"], queryFn: fetchDevRuns, retry: false });
  useEffect(() => {
    if (selectedRunId === null && runs.data?.items[0]) setSelectedRunId(runs.data.items[0].run_id);
  }, [runs.data, selectedRunId]);
  const detail = useQuery({ queryKey: ["dev-run", selectedRunId], queryFn: () => fetchDevRun(selectedRunId ?? ""), enabled: selectedRunId !== null, retry: false });
  const replay = useMutation({ mutationFn: replayDevRun, onSuccess: async (data) => { setSelectedRunId(data.run_id); await queryClient.invalidateQueries({ queryKey: ["dev-runs"] }); } });
  const totals = useMemo(() => ({
    completed: runs.data?.items.filter((item) => item.status === "completed").length ?? 0,
    degraded: runs.data?.items.filter((item) => item.status === "degraded").length ?? 0,
    failed: runs.data?.items.filter((item) => item.status === "failed").length ?? 0,
  }), [runs.data]);

  return <main className="mx-auto max-w-7xl space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="text-sm font-medium text-primary">Developer Console</p><h1 className="mt-1 text-3xl font-semibold">Agent 运行与决策轨迹</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">每个节点、上下文快照、工具调用、事件与终态约束都来自持久化事实。用于解释 Agent 为什么这样做，以及失败后如何收敛。</p></div>
      <div className="flex gap-3"><Button asChild variant="outline"><Link to="/dev/evals">查看评测</Link></Button><Button asChild variant="ghost"><Link to="/me">返回我的</Link></Button></div>
    </header>

    <div className="grid gap-3 sm:grid-cols-4"><Summary label="最近运行" value={runs.data?.items.length ?? 0} icon={Box} /><Summary label="成功" value={totals.completed} icon={CheckCircle2} /><Summary label="降级" value={totals.degraded} icon={AlertTriangle} /><Summary label="失败" value={totals.failed} icon={AlertTriangle} /></div>
    {runs.isPending && <p>正在加载运行记录…</p>}{runs.isError && <p role="alert">无法读取运行记录，请确认当前账号具有开发者权限。</p>}

    <div className="grid gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
      <Card className="h-fit"><CardHeader><CardTitle className="text-base">运行记录</CardTitle></CardHeader><CardContent><ol className="space-y-2">{runs.data?.items.map((run) => <li key={run.run_id}><button className={`w-full rounded-xl border p-3 text-left transition ${selectedRunId === run.run_id ? "border-primary bg-accent/50" : "hover:bg-muted/50"}`} type="button" onClick={() => setSelectedRunId(run.run_id)}><div className="flex items-center justify-between gap-2"><strong className="text-sm">{run.resolved_intent ?? run.result_kind ?? "Agent Run"}</strong><Badge variant={run.status === "failed" ? "destructive" : "secondary"}>{STATUS_LABEL[run.status] ?? run.status}</Badge></div><p className="mt-2 font-mono text-xs text-muted-foreground">{run.run_id.slice(0, 12)}</p><p className="mt-1 text-xs text-muted-foreground">{run.total_latency_ms} ms · {run.total_tokens_in + run.total_tokens_out} tokens</p></button></li>)}</ol></CardContent></Card>

      <section aria-label="Run detail" className="space-y-4">
        {selectedRunId === null && <Card><CardContent className="p-6">选择一个 Run 查看完整轨迹。</CardContent></Card>}
        {detail.isPending && selectedRunId && <Card><CardContent className="p-6">正在加载轨迹…</CardContent></Card>}
        {detail.isError && <p role="alert">无法读取该 Run 的轨迹。</p>}
        {detail.data && <>
          <Card><CardContent className="p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap gap-2"><Badge>{STATUS_LABEL[detail.data.run.status] ?? detail.data.run.status}</Badge><Badge variant="outline">{detail.data.run.result_kind ?? "无结果"}</Badge>{detail.data.run.fallback_reason && <Badge variant="outline">fallback</Badge>}</div><h2 className="mt-3 text-xl font-semibold">{detail.data.request_text}</h2><p className="mt-2 text-xs text-muted-foreground">Graph {detail.data.run.graph_version} · Model {detail.data.run.model_id ?? "未调用模型"}</p></div><Button variant="outline" disabled={replay.isPending || detail.data.run.result_kind !== "resume_optimization"} onClick={() => replay.mutate(detail.data.run.run_id)}><RotateCcw className="mr-2 h-4 w-4" />从冻结快照重新执行</Button></div>{replay.isSuccess && <p role="status" className="mt-3 text-sm text-emerald-700">Replay V2 已进入队列：节点会重新执行，领域工具使用原 Run fixture。</p>}{replay.isError && <p role="alert" className="mt-3 text-sm text-destructive">重放失败；当前 Replay V2 仅支持材料优化 Run，且要求完整冻结快照与工具 fixture。</p>}</CardContent></Card>

          <div className="grid gap-3 md:grid-cols-4"><Fact label="终态唯一" value={detail.data.terminal_invariant.valid ? "通过" : "异常"} icon={CheckCircle2} /><Fact label="总耗时" value={`${detail.data.run.total_latency_ms} ms`} icon={Clock3} /><Fact label="Token" value={String(detail.data.run.total_tokens_in + detail.data.run.total_tokens_out)} icon={Braces} /><Fact label="工具调用" value={String(detail.data.tools.length)} icon={Wrench} /></div>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><Database className="h-4 w-4 text-primary" />上下文选择与冻结快照</CardTitle></CardHeader><CardContent><p className="text-sm text-muted-foreground">Agent 使用的画像、计划、记忆与证据在执行时冻结；后续用户数据变化不会改写本次事实。</p><p className="mt-3 break-all font-mono text-xs">Input SHA-256: {detail.data.input_snapshot?.sha256 ?? "未生成"}</p><details className="mt-3 rounded-xl border p-3"><summary className="cursor-pointer text-sm font-medium">查看脱敏上下文与配置</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify({ input: detail.data.input_snapshot?.data, config: detail.data.config_snapshot.data }, null, 2)}</pre></details></CardContent></Card>
            <Card><CardHeader><CardTitle className="flex items-center gap-2 text-base"><AlertTriangle className="h-4 w-4 text-primary" />终态与失败恢复</CardTitle></CardHeader><CardContent><dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm"><dt>Terminal event</dt><dd>{detail.data.terminal_invariant.terminal_count} 个且位于末尾</dd><dt>Error code</dt><dd>{detail.data.run.error_code ?? "—"}</dd><dt>Fallback</dt><dd>{detail.data.run.fallback_reason ?? "未触发"}</dd><dt>结果</dt><dd>{detail.data.run.result_kind ?? "无可用结果"}</dd></dl><details className="mt-3 rounded-xl border p-3"><summary className="cursor-pointer text-sm font-medium">查看结果快照</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(detail.data.result, null, 2)}</pre></details></CardContent></Card>
          </div>

          <Card><CardHeader><CardTitle className="text-base">决策时间线</CardTitle></CardHeader><CardContent><ol className="relative ml-3 border-l pl-6">{detail.data.steps.map((step) => <li className="relative pb-5 last:pb-0" key={step.sequence}><span className="absolute -left-[31px] top-0.5 h-3 w-3 rounded-full border-2 border-background bg-primary" /><div className="flex flex-wrap items-center gap-2"><strong>{step.sequence}. {step.node_name}</strong><Badge variant="outline">{step.status}</Badge><span className="text-xs text-muted-foreground">{step.latency_ms} ms</span></div>{step.error_code && <p className="mt-1 text-xs text-destructive">{step.error_code}</p>}</li>)}</ol>{detail.data.steps.length === 0 && <p className="text-sm text-muted-foreground">该同步 Run 没有节点级轨迹，仅保留终态事件。</p>}</CardContent></Card>

          <div className="grid gap-4 xl:grid-cols-2"><Card><CardHeader><CardTitle className="text-base">工具调用</CardTitle></CardHeader><CardContent>{detail.data.tools.length ? <ol className="space-y-3">{detail.data.tools.map((tool) => <li className="rounded-xl border p-3" key={tool.tool_call_id}><div className="flex items-center justify-between"><strong>{tool.tool_name}</strong><Badge variant={tool.success ? "secondary" : "destructive"}>{tool.success ? "成功" : tool.error_code}</Badge></div><p className="mt-2 text-xs text-muted-foreground">{tool.provider ?? "local"} · {tool.latency_ms} ms</p></li>)}</ol> : <p className="text-sm text-muted-foreground">本次决策未调用工具；Agent 使用了已构建的本地上下文。</p>}</CardContent></Card><Card><CardHeader><CardTitle className="text-base">持久化事件</CardTitle></CardHeader><CardContent><ol className="max-h-80 space-y-2 overflow-auto">{detail.data.events.map((event) => <li className="flex gap-3 text-sm" key={event.sequence}><span className="w-8 font-mono text-xs text-muted-foreground">{event.sequence}</span><span>{event.event_type}</span></li>)}</ol></CardContent></Card></div>
        </>}
      </section>
    </div>
  </main>;
}

function Summary({ label, value, icon: Icon }: { label: string; value: number; icon: typeof Box }): JSX.Element { return <Card><CardContent className="flex items-center gap-3 p-4"><Icon className="h-5 w-5 text-primary" /><div><strong className="text-2xl">{value}</strong><p className="text-xs text-muted-foreground">{label}</p></div></CardContent></Card>; }
function Fact({ label, value, icon: Icon }: { label: string; value: string; icon: typeof Box }): JSX.Element { return <Card><CardContent className="flex items-center gap-3 p-4"><Icon className="h-5 w-5 text-primary" /><div><strong>{value}</strong><p className="text-xs text-muted-foreground">{label}</p></div></CardContent></Card>; }
