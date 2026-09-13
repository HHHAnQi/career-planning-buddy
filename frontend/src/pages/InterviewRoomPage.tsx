import { FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useFinishInterview, useInterview, useRetryInterviewStart, useSkipInterviewTurn, useSubmitInterviewAnswer, useSubmitInterviewAudio } from "@/api/interviews";
import { useRun } from "@/api/agent-runs";
import { useRunEventStream } from "@/api/sse";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";

export function InterviewRoomPage(): JSX.Element {
  const { interviewId } = useParams(); const query = useInterview(interviewId); const startMutation = useRetryInterviewStart(); const answerMutation = useSubmitInterviewAnswer(); const skipMutation = useSkipInterviewTurn(); const finishMutation = useFinishInterview(); const navigate = useNavigate(); const [searchParams, setSearchParams] = useSearchParams();
  const [answer, setAnswer] = useState(""); const [runId, setRunId] = useState<string | undefined>(() => searchParams.get("run_id") ?? undefined); const activeRunId = runId ?? query.data?.active_run?.run_id; useRunEventStream(activeRunId); const run = useRun(activeRunId);
  const audioMutation = useSubmitInterviewAudio(); const [audio, setAudio] = useState<File>();
  useEffect(() => { if (run.data && ["completed", "degraded", "failed", "cancelled"].includes(run.data.status)) { void query.refetch(); setRunId(undefined); setSearchParams({}, { replace: true }); setAnswer(""); } }, [run.data?.status]);
  const session = query.data; const turn = useMemo(() => session?.turns.find((item) => item.turn_id === session.current_turn_id), [session]);
  useEffect(() => {
    if (session?.status === "completed") navigate(`/interviews/${session.interview_id}/report`, { replace: true });
  }, [navigate, session?.interview_id, session?.status]);
  const trackRun = (value: { run_id: string }) => { setRunId(value.run_id); setSearchParams({ run_id: value.run_id }, { replace: true }); };
  const submit = (event: FormEvent) => { event.preventDefault(); if (!turn || !interviewId) return; answerMutation.mutate({ interviewId, body: { answer_text: answer, turn_id: turn.turn_id, version: turn.version } }, { onSuccess: trackRun }); };
  const retryAnalysis = () => { if (!turn?.answer_text || !interviewId) return; answerMutation.mutate({ interviewId, body: { answer_text: turn.answer_text, turn_id: turn.turn_id, version: turn.version } }, { onSuccess: trackRun }); };
  const retryAfterSkip = () => { if (!turn || !interviewId) return; skipMutation.mutate({ interviewId, turnId: turn.turn_id, body: { version: turn.version } }, { onSuccess: trackRun }); };
  const retryStart = () => { if (!interviewId || !session) return; startMutation.mutate({ interviewId, body: { version: session.version } }, { onSuccess: trackRun }); };
  const waiting = activeRunId !== undefined || session?.status === "report_generating" || turn?.analysis_status === "running";
  const questionPlaceholder = waiting ? "AI 正在处理…" : session?.status === "draft" ? "第一题尚未生成" : "正在恢复面试状态…";
  if (query.isLoading) return <p role="status" className="text-center text-muted-foreground">正在恢复面试状态…</p>;
  if (query.isError || !session) return <div className="mx-auto max-w-xl space-y-3 text-center"><p className="text-destructive">面试状态加载失败。</p><Button onClick={() => void query.refetch()}>重新加载</Button></div>;
  return <div className="mx-auto max-w-3xl space-y-5">
    <div className="flex justify-between text-sm text-muted-foreground"><span>第 {session?.asked_question_count ?? 0} / {session?.question_limit ?? 4} 题</span><span>追问 {session?.followup_count ?? 0} / {session?.followup_limit ?? 2}</span></div>
    <Card><CardHeader><CardTitle>{turn?.question_type === "followup" ? "追问" : "面试问题"}</CardTitle></CardHeader><CardContent><p className="text-lg leading-8">{turn?.question_text ?? questionPlaceholder}</p></CardContent></Card>
    {!turn && session.status === "draft" && !waiting && <Card><CardContent className="space-y-3 p-6"><p className="text-sm text-destructive">第一题尚未生成，可能是模型服务暂时不可用。</p><p className="text-sm text-muted-foreground">你的简历、JD 和面试设置均已保存，无需重新创建面试。</p><Button disabled={startMutation.isPending} onClick={retryStart}>{startMutation.isPending ? "正在重试…" : "重试生成第一题"}</Button></CardContent></Card>}
    {turn && session && turn.answer_status === "pending" && <Card><CardContent className="p-6"><form className="space-y-4" onSubmit={submit}><Textarea aria-label="回答" className="min-h-48" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="写下你的回答。提交后会先保存原文，再启动分析。" required /><div className="flex flex-wrap gap-3"><Button disabled={waiting}>提交回答</Button><Button type="button" variant="outline" disabled={waiting} onClick={() => skipMutation.mutate({ interviewId: interviewId!, turnId: turn.turn_id, body: { version: turn.version } }, { onSuccess: trackRun })}>跳过</Button><Button type="button" variant="ghost" disabled={waiting || session.turns.every((x) => x.answer_status !== "submitted")} onClick={() => finishMutation.mutate({ interviewId: interviewId!, body: { version: session.version } }, { onSuccess: trackRun })}>结束并生成报告</Button></div></form></CardContent></Card>}
    {turn?.answer_status === "pending" && <Card><CardContent className="space-y-3 p-6"><p className="text-sm font-medium">单题语音回答（可选）</p><input aria-label="选择音频" type="file" accept="audio/wav,audio/mpeg,audio/webm,audio/mp4" onChange={(event) => setAudio(event.target.files?.[0])} /><p className="text-xs text-muted-foreground">仅用于同步 ASR；原始音频不会保存。文本框内容作为 ASR 失败时的兜底答案。</p><Button type="button" variant="outline" disabled={!audio || waiting || audioMutation.isPending} onClick={() => audio && interviewId && audioMutation.mutate({ interviewId, turnId: turn.turn_id, version: turn.version, audio, fallbackText: answer }, { onSuccess: trackRun })}>{audioMutation.isPending ? "识别中…" : "上传语音并提交"}</Button></CardContent></Card>}
    {turn?.answer_status === "submitted" && turn.analysis_status === "failed" && <Card><CardContent className="space-y-3 p-6"><p className="text-sm text-destructive">分析失败，但原回答已安全保存。</p><p className="rounded-xl bg-muted p-3 text-sm">{turn.answer_text}</p><Button disabled={answerMutation.isPending || waiting} onClick={retryAnalysis}>重试分析</Button></CardContent></Card>}
    {turn?.answer_status === "skipped" && session?.status === "active" && !waiting && <Card><CardContent className="space-y-3 p-6"><p className="text-sm text-destructive">本题已跳过，但下一题未能生成。</p><div className="flex gap-3"><Button disabled={skipMutation.isPending} onClick={retryAfterSkip}>重试生成下一题</Button><Button variant="outline" disabled={finishMutation.isPending || session.turns.every((x) => x.answer_status !== "submitted")} onClick={() => finishMutation.mutate({ interviewId: interviewId!, body: { version: session.version } }, { onSuccess: trackRun })}>结束并生成报告</Button></div></CardContent></Card>}
    {waiting && <p role="status" className="text-center text-sm text-muted-foreground">AI 正在分析，页面刷新后也可恢复。</p>}
    {(startMutation.isError || answerMutation.isError || skipMutation.isError || finishMutation.isError) && <p className="text-sm text-destructive">操作失败，请根据当前题目状态重试。</p>}
  </div>;
}
