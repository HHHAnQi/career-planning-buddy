import { useState } from "react";
import { ArrowLeft, Brain, EyeOff, ShieldCheck, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useDecideCandidate, useDeleteMemory, useMemories, useMemoryCandidates, usePatchMemory } from "@/api/memories";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toUserFacingError } from "@/lib/errors";

const TYPE_LABEL: Record<string, string> = { profile_fact: "目标与背景", stable_preference: "稳定偏好", execution_pattern: "执行规律" };
const SENSITIVITY_LABEL: Record<string, string> = { normal: "常规", sensitive: "敏感", highly_sensitive: "高度敏感" };

export function MemoriesPage(): JSX.Element {
  const active = useMemories("active");
  const closed = useMemories("closed");
  const candidates = useMemoryCandidates();
  const pendingCandidates = (candidates.data?.items ?? []).filter((item) => item.status === "pending");

  if (active.isLoading || closed.isLoading || candidates.isLoading) {
    return <div className="text-sm text-muted-foreground">正在加载搭子记忆…</div>;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link to="/me" className="inline-flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="h-4 w-4" />返回我的</Link>
      <header>
        <p className="text-sm font-medium text-primary">由你控制</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">搭子记住了什么</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">这些信息用于控制任务强度和延续上次进展。敏感信息只有在你确认后才会保留。</p>
      </header>

      {pendingCandidates.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-primary" /><h2 className="text-lg font-semibold">需要你确认</h2><Badge>{pendingCandidates.length}</Badge></div>
          {pendingCandidates.map((candidate) => <CandidateCard key={candidate.candidate_id} candidate={candidate} />)}
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">正在使用的记忆</h2>
        {(active.data?.items.length ?? 0) === 0 ? (
          <Card className="border-dashed"><CardContent className="flex flex-col items-start gap-3 p-6"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent text-primary"><Brain className="h-5 w-5" /></span><div><div className="text-sm font-medium">暂时没有长期记忆</div><p className="mt-1 text-sm leading-6 text-muted-foreground">候选提取功能接通后，系统会先征得你的同意，再保留敏感事实或稳定偏好。</p></div></CardContent></Card>
        ) : (
          <div className="space-y-3">{active.data?.items.map((memory) => <MemoryCard key={memory.memory_id} memory={memory} />)}</div>
        )}
      </section>

      {(closed.data?.items.length ?? 0) > 0 && (
        <details className="group rounded-2xl border bg-card">
          <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between px-5 text-sm font-medium"><span className="flex items-center gap-2"><EyeOff className="h-4 w-4 text-muted-foreground" />已停用的记忆</span><Badge variant="secondary">{closed.data?.items.length}</Badge></summary>
          <div className="space-y-3 border-t p-3 sm:p-4">{closed.data?.items.map((memory) => <MemoryCard key={memory.memory_id} memory={memory} />)}</div>
        </details>
      )}
    </div>
  );
}

function CandidateCard({ candidate }: { candidate: NonNullable<ReturnType<typeof useMemoryCandidates>["data"]>["items"][number] }): JSX.Element {
  const decide = useDecideCandidate();
  const error = decide.error === null ? null : toUserFacingError(decide.error);
  return (
    <Card className="border-primary/20">
      <CardHeader className="p-5 pb-3"><div className="flex flex-wrap items-start justify-between gap-2"><CardTitle className="text-base leading-6">{candidate.summary}</CardTitle><Badge variant="warning">{SENSITIVITY_LABEL[candidate.sensitivity]}</Badge></div><CardDescription>{TYPE_LABEL[candidate.memory_type]} · 未确认不会进入长期记忆</CardDescription></CardHeader>
      <CardContent className="space-y-3 p-5 pt-0">{error && <p className="text-sm text-destructive">{error.message}</p>}<div className="flex gap-2"><Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate({ candidateId: candidate.candidate_id, decision: "confirm" })}>同意记住</Button><Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => decide.mutate({ candidateId: candidate.candidate_id, decision: "reject" })}>不保留</Button></div></CardContent>
    </Card>
  );
}

function MemoryCard({ memory }: { memory: NonNullable<ReturnType<typeof useMemories>["data"]>["items"][number] }): JSX.Element {
  const patch = usePatchMemory();
  const remove = useDeleteMemory();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const nextStatus = memory.status === "active" ? "closed" : "active";
  const error = patch.error ?? remove.error;
  return (
    <Card>
      <CardHeader className="p-5 pb-3"><div className="flex flex-wrap items-start justify-between gap-2"><CardTitle className="text-base leading-6">{memory.summary}</CardTitle><div className="flex gap-2"><Badge variant="outline">{SENSITIVITY_LABEL[memory.sensitivity]}</Badge><Badge variant={memory.status === "active" ? "success" : "secondary"}>{memory.status === "active" ? "使用中" : "已停用"}</Badge></div></div><CardDescription>{TYPE_LABEL[memory.memory_type]}</CardDescription></CardHeader>
      <CardContent className="space-y-3 p-5 pt-0">
        {error && <p className="text-sm text-destructive">{toUserFacingError(error).message}</p>}
        <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" disabled={patch.isPending} onClick={() => patch.mutate({ memoryId: memory.memory_id, payload: { status: nextStatus, version: memory.version } })}>{memory.status === "active" ? "暂停使用" : "重新启用"}</Button><Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => setConfirmDelete(true)}><Trash2 className="h-4 w-4" />删除</Button></div>
      </CardContent>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}><DialogContent><DialogHeader><DialogTitle>删除这条记忆？</DialogTitle><DialogDescription>删除后无法恢复，后续计划也不会再使用它。</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setConfirmDelete(false)}>取消</Button><Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate(memory.memory_id, { onSuccess: () => setConfirmDelete(false) })}>确认删除</Button></DialogFooter></DialogContent></Dialog>
    </Card>
  );
}
