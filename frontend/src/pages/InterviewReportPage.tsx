import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  useConfirmInterviewTraining,
  useCreateInterviewMemoryCandidates,
  useCreateRetest,
  useInterview,
  usePreviewInterviewTraining,
  useRetryInterviewReport,
} from "@/api/interviews";
import { Button } from "@/components/ui/button";
import { useCreateResumeAssessment } from "@/api/resumes";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { markInterviewReportSeen } from "@/lib/interview-report";

const COMPARISON_LABEL = {
  improved: "已改善",
  unchanged: "暂未变化",
  regressed: "有所退步",
  insufficient_comparable_evidence: "证据不可比",
} as const;

const DIMENSION_LABEL: Record<string, string> = {
  technical_accuracy: "技术准确性",
  answer_structure: "回答结构",
  evidence_quality: "证据质量",
  role_match: "岗位匹配度",
  communication: "表达沟通",
};
const VERDICT_LABEL: Record<string, string> = {
  correct: "准确",
  incorrect: "需要纠正",
  partially_correct: "部分准确",
  insufficient_evidence: "证据不足",
  supported: "有证据支持",
  partially_supported: "部分支持",
  unsupported: "暂不支持",
};

export function InterviewReportPage(): JSX.Element {
  const { interviewId } = useParams();
  const navigate = useNavigate();
  const query = useInterview(interviewId);
  const retry = useRetryInterviewReport();
  const createCandidates = useCreateInterviewMemoryCandidates();
  const previewTraining = usePreviewInterviewTraining();
  const confirmTraining = useConfirmInterviewTraining();
  const createRetest = useCreateRetest();
  const createAssessment = useCreateResumeAssessment();
  const [selectedActions, setSelectedActions] = useState<number[]>([]);
  const [longTermWeaknesses, setLongTermWeaknesses] = useState<string[]>([]);
  const session = query.data;
  const report = session?.report;
  useEffect(() => {
    if (session?.report_status === "ready") markInterviewReportSeen(session.interview_id);
  }, [session?.interview_id, session?.report_status]);
  if (query.isLoading) return <p role="status" className="text-center text-muted-foreground">正在加载面试报告…</p>;
  if (query.isError || !session) return <div className="mx-auto max-w-xl space-y-3 text-center"><p className="text-destructive">报告加载失败。</p><Button onClick={() => void query.refetch()}>重新加载</Button></div>;
  if (session.report_status === "failed") return <Card className="mx-auto max-w-xl"><CardContent className="space-y-4 p-8"><p>报告生成失败，逐题回答和分析已保留。</p><Button disabled={retry.isPending} onClick={() => retry.mutate({ interviewId: interviewId!, body: { version: session.version } })}>{retry.isPending ? "正在重试…" : "重试报告"}</Button></CardContent></Card>;
  if (!report) return <p role="status" className="text-center text-muted-foreground">报告正在生成，请稍候；刷新页面不会丢失进度。</p>;

  function toggleAction(index: number): void {
    setSelectedActions((current) => current.includes(index) ? current.filter((item) => item !== index) : current.length < 3 ? [...current, index] : current);
    previewTraining.reset();
  }

  function toggleWeakness(key: string): void {
    setLongTermWeaknesses((current) => current.includes(key) ? current.filter((item) => item !== key) : current.length < 3 ? [...current, key] : current);
  }

  const evidenceLabel = (turnId: string): string => {
    const turn = session.turns.find((item) => item.turn_id === turnId);
    return turn ? `第 ${turn.ordinal} 题：${turn.question_text.slice(0, 32)}${turn.question_text.length > 32 ? "…" : ""}` : "历史证据题目";
  };

  return <div className="mx-auto max-w-4xl space-y-6">
    <div><h1 className="text-2xl font-semibold">面试报告</h1><p className="mt-2 text-muted-foreground">{report.overall_summary}</p></div>
    <Card className="border-primary/20 bg-gradient-to-br from-accent/35 to-card">
      <CardHeader>
        <CardTitle>Agent 下一步建议</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm leading-6 text-muted-foreground">这份报告不是终点。搭子会把证据化弱点回流到训练路线、长期记忆、材料验证和复测中。</p>
        <div className="flex flex-wrap gap-3">
          <Button variant="outline" disabled={selectedActions.length === 0} onClick={() => previewTraining.mutate({ interviewId: interviewId!, actionIndexes: selectedActions })}>把已选训练加入路线</Button>
          <Button variant="outline" disabled={longTermWeaknesses.length === 0 || createCandidates.isPending} onClick={() => createCandidates.mutate({ interviewId: interviewId!, weaknessKeys: longTermWeaknesses })}>保存为待确认记忆</Button>
          <Button variant="outline" disabled={createAssessment.isPending} onClick={() => createAssessment.mutate({ resume_version_id: session.resume_version_id, job_target_id: session.job_target_id, interview_session_id: session.interview_id }, { onSuccess: (data) => navigate(`/materials?run_id=${data.run_id}`) })}>启动材料优化 Agent</Button>
          {!report.comparison && <Button variant="ghost" disabled={createRetest.isPending} onClick={() => createRetest.mutate({ interviewId: interviewId!, weaknessKeys: report.weaknesses.map((item) => item.weakness_key).slice(0, 3) }, { onSuccess: (data) => navigate(`/interviews/${data.interview_id}?run_id=${data.run_id}`) })}>安排复测</Button>}
        </div>
      </CardContent>
    </Card>
    {report.strengths.length > 0 && <section className="space-y-3"><h2 className="text-xl font-semibold">做得好的地方</h2><Card><CardContent className="p-5"><ul className="list-disc space-y-2 pl-5 text-sm leading-6">{report.strengths.map((strength) => <li key={strength}>{strength}</li>)}</ul></CardContent></Card></section>}
    <section className="space-y-3"><h2 className="text-xl font-semibold">最值得改进</h2>{report.weaknesses.map((weakness) => <Card key={weakness.weakness_key}><CardHeader><CardTitle>{weakness.topic}</CardTitle></CardHeader><CardContent className="space-y-3"><p>维度：{DIMENSION_LABEL[weakness.dimension] ?? weakness.dimension} · 置信度 {Math.round(weakness.confidence * 100)}%</p><ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">{weakness.evidence_turn_ids.map((turnId) => <li key={turnId}>{evidenceLabel(turnId)}</li>)}</ul><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={longTermWeaknesses.includes(weakness.weakness_key)} onChange={() => toggleWeakness(weakness.weakness_key)} />长期关注这个问题</label></CardContent></Card>)}</section>
    {longTermWeaknesses.length > 0 && <div className="flex items-center gap-3"><Button variant="outline" disabled={createCandidates.isPending} onClick={() => createCandidates.mutate({ interviewId: interviewId!, weaknessKeys: longTermWeaknesses })}>生成待确认长期记忆</Button>{createCandidates.isSuccess && <span className="text-sm text-muted-foreground">已进入“我的 → 记忆”确认，不会自动长期保存。</span>}</div>}
    <section className="space-y-3"><h2 className="text-xl font-semibold">逐题分析</h2>{session.turns.filter((item) => item.analysis).map((turn) => <Card key={turn.turn_id}><CardHeader><CardTitle>第 {turn.ordinal} 题</CardTitle></CardHeader><CardContent className="space-y-3"><p>{turn.question_text}</p><p className="rounded-xl bg-muted p-3 text-sm">原回答：{turn.answer_text}</p>{turn.analysis!.missing_key_points.length > 0 && <p className="text-sm"><strong>缺失要点：</strong>{turn.analysis!.missing_key_points.join("；")}</p>}{turn.analysis!.factual_findings.map((finding, index) => <div className="rounded-xl border p-3 text-sm" key={`${finding.claim}-${index}`}><strong>{VERDICT_LABEL[finding.verdict] ?? finding.verdict}：{finding.claim}</strong><p className="mt-1 text-muted-foreground">{finding.rationale}</p></div>)}<p className="text-sm"><strong>改进动作：</strong>{turn.analysis!.improvement_actions.join("；")}</p>{turn.analysis!.suggested_outline.length > 0 && <p className="text-sm"><strong>建议回答顺序：</strong>{turn.analysis!.suggested_outline.join(" → ")}</p>}{turn.analysis!.limitations.length > 0 && <p className="text-xs text-muted-foreground">本题限制：{turn.analysis!.limitations.join("；")}</p>}</CardContent></Card>)}</section>
    <section className="space-y-3"><h2 className="text-xl font-semibold">训练建议</h2>{report.recommended_training_actions.map((action, index) => <Card key={`${action.title}-${index}`} className={selectedActions.includes(index) ? "border-primary" : ""}><CardContent className="flex gap-3 p-5"><input aria-label={`选择${action.title}`} type="checkbox" checked={selectedActions.includes(index)} onChange={() => toggleAction(index)} /><div><strong>{action.title}</strong><p className="mt-1 text-sm">{action.starter_action}</p><p className="text-sm text-muted-foreground">交付物：{action.deliverable} · {action.estimated_minutes} 分钟</p></div></CardContent></Card>)}</section>
    {selectedActions.length > 0 && <Card><CardContent className="space-y-3 p-5"><p className="text-sm">一次确认最多加入 3 个训练动作。确认前不会改变当前计划。</p>{previewTraining.data === undefined ? <Button onClick={() => previewTraining.mutate({ interviewId: interviewId!, actionIndexes: selectedActions })}>预览加入训练计划</Button> : <><p className="text-sm font-medium">{previewTraining.data.mode === "task_adjustment" ? "将调整当前周期中未完成的任务，周期边界不变。" : "当前周期无法容纳所选训练，将创建带报告来源的新计划 Run；旧计划不会在确认前被替换。"}</p><Button disabled={confirmTraining.isPending} onClick={() => confirmTraining.mutate({ interviewId: interviewId!, actionIndexes: selectedActions })}>{confirmTraining.isPending ? "正在确认…" : "确认加入训练计划"}</Button></>}{confirmTraining.isSuccess && <div className="flex flex-wrap items-center gap-3 text-sm text-primary"><span>训练动作已确认：{confirmTraining.data.mode === "task_adjustment" ? "当前任务已更新" : "新计划正在生成"}。</span>{confirmTraining.data.run && <Button asChild size="sm" variant="outline"><Link to={`/today?run_id=${confirmTraining.data.run.run_id}`}>查看生成进度</Link></Button>}</div>}</CardContent></Card>}
    {report.comparison && <section className="space-y-3"><h2 className="text-xl font-semibold">跨场次改善</h2>{report.comparison.items.map((item) => <Card key={item.weakness_key}><CardContent className="p-5"><strong>{item.topic}</strong><p className="mt-1 text-sm">{COMPARISON_LABEL[item.status]}</p><p className="text-xs text-muted-foreground">基线证据 {item.baseline_evidence_turn_ids.length} 条 · 本次可比证据 {item.current_evidence_turn_ids.length} 条</p></CardContent></Card>)}</section>}
    {!report.comparison && <Button variant="outline" disabled={createRetest.isPending} onClick={() => createRetest.mutate({ interviewId: interviewId!, weaknessKeys: report.weaknesses.map((item) => item.weakness_key).slice(0, 3) }, { onSuccess: (data) => navigate(`/interviews/${data.interview_id}?run_id=${data.run_id}`) })}>{createRetest.isPending ? "正在创建复测…" : "针对薄弱点开始复测"}</Button>}
    <section className="space-y-3"><h2 className="text-xl font-semibold">简历主张与语音指标</h2><Button variant="outline" disabled={createAssessment.isPending} onClick={() => createAssessment.mutate({ resume_version_id: session.resume_version_id, job_target_id: session.job_target_id, interview_session_id: session.interview_id }, { onSuccess: (data) => navigate(`/materials?run_id=${data.run_id}`) })}>{createAssessment.isPending ? "正在创建 Agent Run…" : "用本场证据优化简历"}</Button>{createAssessment.data && <p className="text-sm text-primary">优化任务已进入持久化队列，可前往求职材料查看进度。</p>}{session.turns.filter((turn) => turn.audio_analysis).map((turn) => <Card key={`audio-${turn.turn_id}`}><CardContent className="space-y-1 p-4"><strong>第 {turn.ordinal} 题语音表达</strong><p className="text-sm">时长 {turn.audio_analysis?.duration_seconds ?? "未知"} 秒 · 有效语速 {turn.audio_analysis?.effective_words_per_minute ?? "不可计算"} · 长停顿 {turn.audio_analysis?.long_pause_count ?? "不可计算"}</p><p className="text-xs text-muted-foreground">{turn.audio_analysis?.limitations.join("；")}</p></CardContent></Card>)}</section>
    {(retry.isError || createCandidates.isError || previewTraining.isError || confirmTraining.isError || createRetest.isError || createAssessment.isError) && <p className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive">操作没有完成，已保留当前选择，请稍后重试。</p>}
    {report.limitations.length > 0 && <p className="text-sm text-muted-foreground">说明：{report.limitations.join("；")}</p>}
  </div>;
}
