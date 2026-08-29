import { FormEvent, ReactNode, useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, FileText, GitBranch, Sparkles, Target, XCircle } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { ApiError } from "@/api/client";
import { useRun } from "@/api/agent-runs";
import {
  useApplyResumeRewritesBatch,
  useCreateResumeAssessment,
  useCreateJobTarget,
  useCreateResumeVersion,
  useDecideResumeRewrite,
  useDeleteJobTarget,
  useDeleteResumeVersion,
  useExtractResumeDocument,
  useJobTargets,
  useResumeAssessments,
  useResumeAssessment,
  useResumeVersions,
} from "@/api/resumes";
import type { ResumeClaimFinding } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const VERDICT_LABEL: Record<string, string> = {
  supported: "证据充分",
  partially_supported: "部分支持",
  unsupported: "存在冲突",
  insufficient_evidence: "证据不足",
};

export function MaterialsPage(): JSX.Element {
  const resumes = useResumeVersions();
  const targets = useJobTargets();
  const assessments = useResumeAssessments();
  const createResume = useCreateResumeVersion();
  const extractResume = useExtractResumeDocument();
  const createTarget = useCreateJobTarget();
  const deleteResume = useDeleteResumeVersion();
  const deleteTarget = useDeleteJobTarget();
  const decideRewrite = useDecideResumeRewrite();
  const applyBatch = useApplyResumeRewritesBatch();
  const createAssessment = useCreateResumeAssessment();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const runId = searchParams.get("run_id") ?? undefined;
  const run = useRun(runId);
  const runAssessmentId = run.data?.result && "assessment_id" in run.data.result
    ? run.data.result.assessment_id
    : undefined;
  const runAssessment = useResumeAssessment(runAssessmentId);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [showBatchPreview, setShowBatchPreview] = useState(false);
  const [resume, setResume] = useState<{
    label: string;
    source_text: string;
    source_type: "pasted_text" | "uploaded_file";
    source_filename?: string;
    source_media_type?: string;
  }>({ label: "我的简历", source_text: "", source_type: "pasted_text" });
  const [target, setTarget] = useState({ title: "", company: "", jd_text: "" });

  const latestAssessment = runId
    ? runAssessment.data ?? null
    : assessments.data?.[0] ?? null;
  const decisionByClaim = useMemo(
    () => new Map(latestAssessment?.rewrite_decisions.map((item) => [item.claim_id, item]) ?? []),
    [latestAssessment],
  );
  const pendingSuggestions = latestAssessment?.claims.filter(
    (claim) => claim.suggested_rewrite && !decisionByClaim.has(claim.claim_id),
  ).length ?? 0;
  const acceptedClaimIds = latestAssessment?.rewrite_decisions
    .filter((item) => item.status === "accepted")
    .map((item) => item.claim_id) ?? [];

  const saveResume = (event: FormEvent) => {
    event.preventDefault();
    createResume.mutate(resume, {
      onSuccess: () => setResume({ label: "我的简历", source_text: "", source_type: "pasted_text" }),
    });
  };
  const selectResumeFile = (file: File | undefined) => {
    if (!file) return;
    extractResume.mutate(file, {
      onSuccess: (result) => setResume({
        label: resume.label === "我的简历" ? file.name.replace(/\.(pdf|docx|txt)$/i, "") : resume.label,
        source_text: result.source_text,
        source_type: "uploaded_file",
        source_filename: result.filename,
        source_media_type: result.media_type,
      }),
    });
  };
  const saveTarget = (event: FormEvent) => {
    event.preventDefault();
    createTarget.mutate(target, { onSuccess: () => setTarget({ title: "", company: "", jd_text: "" }) });
  };

  return <div className="mx-auto max-w-6xl space-y-6">
    <header className="rounded-3xl border bg-gradient-to-br from-primary/10 via-background to-accent/40 p-6 sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div className="max-w-2xl">
          <Badge variant="secondary" className="mb-3"><Sparkles className="mr-1 h-3.5 w-3.5" />Agent 优化中心</Badge>
          <h1 className="text-3xl font-semibold tracking-tight">让材料、岗位和面试证据一起工作</h1>
          <p className="mt-3 leading-7 text-muted-foreground">搭子会用目标 JD 和真实面试回答核验简历主张，再把建议交给你确认。任何改写都不会自动覆盖原简历。</p>
        </div>
        <Button asChild><Link to="/interviews/new">用当前材料开始面试<ArrowRight className="ml-2 h-4 w-4" /></Link></Button>
      </div>
      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <Metric icon={FileText} label="简历版本" value={resumes.data?.items.length ?? 0} />
        <Metric icon={Target} label="目标岗位" value={targets.data?.items.length ?? 0} />
        <Metric icon={Sparkles} label="待确认建议" value={pendingSuggestions} />
      </div>
    </header>

    {searchParams.get("next") === "/interviews/new" && resumes.data?.items.length && targets.data?.items.length ? <Card className="border-primary/25 bg-accent/40"><CardContent className="flex flex-wrap items-center justify-between gap-3 p-5"><p className="text-sm">材料已经齐全，可以继续刚才的面试设置。</p><Button asChild><Link to="/interviews/new">继续设置面试</Link></Button></CardContent></Card> : null}

    {runId && <Card className="border-primary/25 bg-accent/35"><CardContent className="flex flex-wrap items-center justify-between gap-4 p-5"><div><strong>{run.data?.status === "completed" ? "材料优化已完成" : run.data?.status === "failed" ? "材料优化未完成" : "材料优化 Agent 正在运行"}</strong><p className="mt-1 text-sm text-muted-foreground">{run.data?.status === "completed" ? "已按本次 Run 的结果资源精确加载建议。" : run.data?.status === "failed" ? `错误码：${run.data.error_code ?? "未知"}` : "任务由持久化运行时执行，刷新页面不会丢失。"}</p></div>{run.data?.status === "completed" && runAssessment.isLoading && <span className="text-sm text-muted-foreground">正在加载本次结果…</span>}</CardContent></Card>}

    <section className="space-y-3">
      <div><h2 className="text-xl font-semibold">材料资产</h2><p className="text-sm text-muted-foreground">先冻结输入，Agent 的判断才能回溯到具体版本。</p></div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card><CardHeader><CardTitle>简历</CardTitle><CardDescription>上传 PDF、DOCX、TXT，或粘贴文本；保存前可检查解析结果。</CardDescription></CardHeader><CardContent>
          <form className="space-y-3" onSubmit={saveResume}>
            <Input aria-label="简历名称" value={resume.label} onChange={(e) => setResume({ ...resume, label: e.target.value })} required />
            <div className="rounded-xl border border-dashed p-4"><label className="block text-sm font-medium" htmlFor="resume-file">上传简历文件</label><p className="mt-1 text-xs text-muted-foreground">支持 PDF、DOCX、TXT，最大 5 MiB。</p><Input id="resume-file" aria-label="上传简历文件" className="mt-3" type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" disabled={extractResume.isPending} onChange={(e) => selectResumeFile(e.target.files?.[0])} />{extractResume.isPending && <p role="status" className="mt-2 text-sm text-muted-foreground">正在解析简历…</p>}{extractResume.isError && <p className="mt-2 text-sm text-destructive">{resumeExtractError(extractResume.error)}</p>}{resume.source_type === "uploaded_file" && resume.source_filename && <p className="mt-2 text-sm text-emerald-700">已解析 {resume.source_filename}，请检查下方文本后保存。</p>}</div>
            <div><label className="mb-2 block text-sm font-medium" htmlFor="resume-text">简历文本与解析预览</label><Textarea id="resume-text" aria-label="简历文本" className="min-h-44" placeholder="也可以直接粘贴简历文本" value={resume.source_text} onChange={(e) => setResume({ ...resume, source_text: e.target.value })} minLength={20} required /></div>
            {createResume.isError && <p className="text-sm text-destructive">简历保存失败，请检查内容后重试。</p>}
            <Button disabled={createResume.isPending || resume.label.trim().length === 0 || resume.source_text.trim().length < 20}>{createResume.isPending ? "保存中…" : "保存简历版本"}</Button>
          </form>
          <AssetList loading={resumes.isLoading} error={resumes.isError} empty="还没有简历版本。">{resumes.data?.items.map((item) => <div className="rounded-xl border p-3" key={item.resume_version_id}><div className="flex items-center justify-between gap-3"><span><strong>{item.label}</strong>{item.parent_version_id && <Badge variant="outline" className="ml-2">优化版本</Badge>}</span><Button type="button" size="sm" variant="ghost" disabled={deleteResume.isPending} onClick={() => window.confirm("移除这个简历版本？历史记录仍保留冻结内容。") && deleteResume.mutate(item.resume_version_id)}>移除</Button></div><p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{item.source_text}</p></div>)}</AssetList>
        </CardContent></Card>

        <Card><CardHeader><CardTitle>目标 JD</CardTitle><CardDescription>岗位要求同样冻结，避免后续变化污染历史结论。</CardDescription></CardHeader><CardContent>
          <form className="space-y-3" onSubmit={saveTarget}><Input aria-label="岗位名称" placeholder="岗位名称" value={target.title} onChange={(e) => setTarget({ ...target, title: e.target.value })} required /><Input aria-label="公司" placeholder="公司（可选）" value={target.company} onChange={(e) => setTarget({ ...target, company: e.target.value })} /><Textarea aria-label="JD 文本" className="min-h-44" value={target.jd_text} onChange={(e) => setTarget({ ...target, jd_text: e.target.value })} minLength={20} required />{createTarget.isError && <p className="text-sm text-destructive">目标岗位保存失败，请检查内容后重试。</p>}<Button disabled={createTarget.isPending || target.title.trim().length === 0 || target.jd_text.trim().length < 20}>{createTarget.isPending ? "保存中…" : "保存目标岗位"}</Button></form>
          <AssetList loading={targets.isLoading} error={targets.isError} empty="还没有目标岗位。">{targets.data?.items.map((item) => <div className="rounded-xl border p-3" key={item.job_target_id}><div className="flex items-center justify-between gap-3"><strong>{item.title}{item.company ? ` · ${item.company}` : ""}</strong><Button type="button" size="sm" variant="ghost" disabled={deleteTarget.isPending} onClick={() => window.confirm("移除这个目标岗位？历史记录仍保留冻结内容。") && deleteTarget.mutate(item.job_target_id)}>移除</Button></div><p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{item.jd_text}</p></div>)}</AssetList>
        </CardContent></Card>
      </div>
    </section>

    <section className="space-y-3">
      <div><h2 className="text-xl font-semibold">Agent 优化建议</h2><p className="text-sm text-muted-foreground">建议来自“简历版本 + 目标 JD + 面试原回答”，你决定哪些内容进入下一版本。</p></div>
      {assessments.isLoading && <Card><CardContent className="p-6 text-sm text-muted-foreground">正在加载评估结果…</CardContent></Card>}
      {!assessments.isLoading && !latestAssessment && <Card className="border-dashed"><CardContent className="flex flex-wrap items-center justify-between gap-4 p-6"><div><strong>还没有可用的材料评估</strong><p className="mt-1 text-sm text-muted-foreground">可以先用简历和 JD 做面试前诊断；完成面试后再用真实回答增强证据。</p></div><div className="flex flex-wrap gap-2"><Button variant="outline" disabled={createAssessment.isPending || !resumes.data?.items[0] || !targets.data?.items[0]} onClick={() => { const resumeItem = resumes.data?.items[0]; const targetItem = targets.data?.items[0]; if (resumeItem && targetItem) createAssessment.mutate({ resume_version_id: resumeItem.resume_version_id, job_target_id: targetItem.job_target_id, interview_session_id: null }, { onSuccess: (data) => navigate(`/materials?run_id=${data.run_id}`) }); }}>{createAssessment.isPending ? "正在启动…" : "先做材料诊断"}</Button><Button asChild variant="outline"><Link to="/interviews">用面试补充证据</Link></Button></div></CardContent></Card>}
      {latestAssessment && <div className="grid gap-4">
        {latestAssessment.context_manifest && <Card><CardContent className="grid gap-4 p-5 sm:grid-cols-4"><Metric icon={Sparkles} label="上下文候选" value={latestAssessment.context_manifest.candidates.length} /><Metric icon={CheckCircle2} label="入选证据" value={latestAssessment.context_manifest.selected_evidence_refs.length} /><Metric icon={FileText} label="实际 Prompt Token" value={latestAssessment.context_manifest.actual_prompt_tokens ?? latestAssessment.context_manifest.used_tokens} /><Metric icon={XCircle} label="注入过滤" value={latestAssessment.context_manifest.prompt_injection_filtered_count} /><p className="sm:col-span-4 text-xs text-muted-foreground">{latestAssessment.interview_session_id ? "面试后证据增强" : "面试前材料诊断"} · {latestAssessment.context_manifest.algorithm_version} · Embedding {latestAssessment.context_manifest.embedding_provider ?? "未记录"}</p><details className="sm:col-span-4"><summary className="cursor-pointer text-sm font-medium">查看入选证据与选择原因</summary><ul className="mt-3 space-y-2 text-xs text-muted-foreground">{latestAssessment.context_manifest.candidates.filter((item) => item.selected).map((item) => <li className="rounded-lg border p-3" key={item.evidence_ref}><strong>{item.source_type} · {item.source_id}</strong><p className="mt-1">{item.rendered_content}</p><p className="mt-1">{item.selection_reason}</p></li>)}</ul></details></CardContent></Card>}
        {acceptedClaimIds.length > 0 && <Card className="border-primary/25"><CardContent className="space-y-4 p-5"><div className="flex flex-wrap items-center justify-between gap-4"><div><strong>已接受 {acceptedClaimIds.length} 条改写</strong><p className="mt-1 text-sm text-muted-foreground">先检查统一变更清单，再一次确认生成一个子版本。</p></div><Button variant="outline" disabled={applyBatch.isPending} onClick={() => setShowBatchPreview((value) => !value)}><GitBranch className="mr-2 h-4 w-4" />{showBatchPreview ? "收起变更预览" : "预览全部变更"}</Button></div>{showBatchPreview && <div className="space-y-3 border-t pt-4"><h3 className="font-medium">最终版本变更清单</h3>{acceptedClaimIds.map((claimId) => { const claim = latestAssessment.claims.find((item) => item.claim_id === claimId); const decision = latestAssessment.rewrite_decisions.find((item) => item.claim_id === claimId); if (!claim || !decision) return null; return <div className="grid gap-2 rounded-xl border p-3 text-sm sm:grid-cols-2" key={claimId}><div><span className="text-xs text-muted-foreground">原文</span><p className="mt-1">{claim.claim_text}</p></div><div><span className="text-xs text-muted-foreground">确认稿</span><p className="mt-1 text-primary">{decision.rewrite_text}</p></div></div>; })}<div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-accent/40 p-4"><p className="text-sm">父版本保持不变；本次只创建 1 个可追溯子版本。</p><Button disabled={applyBatch.isPending} onClick={() => applyBatch.mutate({ assessmentId: latestAssessment.assessment_id, claimIds: acceptedClaimIds }, { onSuccess: () => setShowBatchPreview(false) })}>{applyBatch.isPending ? "正在生成…" : "确认并生成新版本"}</Button></div></div>}</CardContent></Card>}
        {latestAssessment.claims.map((claim) => {
          const decision = decisionByClaim.get(claim.claim_id);
          return <RewriteCard key={claim.claim_id} claim={claim} decision={decision} draft={drafts[claim.claim_id] ?? claim.suggested_rewrite ?? ""} onDraft={(value) => setDrafts((current) => ({ ...current, [claim.claim_id]: value }))} onAccept={() => decideRewrite.mutate({ assessmentId: latestAssessment.assessment_id, claimId: claim.claim_id, status: "accepted", rewriteText: drafts[claim.claim_id] ?? claim.suggested_rewrite ?? "" })} onReject={() => decideRewrite.mutate({ assessmentId: latestAssessment.assessment_id, claimId: claim.claim_id, status: "rejected" })} busy={decideRewrite.isPending || applyBatch.isPending} />;
        })}
        <p className="text-xs text-muted-foreground">{latestAssessment.limitations.join(" · ")}</p>
      </div>}
    </section>
  </div>;
}

function Metric({ icon: Icon, label, value }: { icon: typeof FileText; label: string; value: number }): JSX.Element {
  return <div className="flex items-center gap-3 rounded-2xl border bg-background/80 p-4"><Icon className="h-5 w-5 text-primary" /><div><strong className="text-xl">{value}</strong><p className="text-xs text-muted-foreground">{label}</p></div></div>;
}

function AssetList({ loading, error, empty, children }: { loading: boolean; error: boolean; empty: string; children: ReactNode }): JSX.Element {
  return <div className="mt-5 space-y-2">{loading && <p role="status" className="text-sm text-muted-foreground">正在加载…</p>}{error && <p className="text-sm text-destructive">加载失败，请刷新重试。</p>}{!loading && !error && !children && <p className="text-sm text-muted-foreground">{empty}</p>}{children}</div>;
}

function RewriteCard({ claim, decision, draft, onDraft, onAccept, onReject, busy }: { claim: ResumeClaimFinding; decision?: { status: "accepted" | "rejected" | "applied"; rewrite_text: string | null }; draft: string; onDraft: (value: string) => void; onAccept: () => void; onReject: () => void; busy: boolean }): JSX.Element {
  return <Card className={decision?.status === "applied" ? "border-emerald-300" : ""}><CardContent className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
    <div className="space-y-3"><div className="flex flex-wrap items-center gap-2"><Badge variant={claim.verdict === "supported" ? "secondary" : "outline"}>{VERDICT_LABEL[claim.verdict] ?? claim.verdict}</Badge>{decision && <Badge>{decision.status === "applied" ? "已生成新版本" : decision.status === "accepted" ? "已接受" : "已拒绝"}</Badge>}</div><div><p className="text-xs text-muted-foreground">原简历主张</p><p className="mt-1 font-medium leading-6">{claim.claim_text}</p></div><p className="text-sm leading-6 text-muted-foreground">{claim.rationale}</p><div className="flex gap-4 text-xs text-muted-foreground"><span>岗位要求 {claim.requirement_ids.length}</span><span>面试证据 {claim.evidence_turn_ids.length}</span></div></div>
    <div className="rounded-2xl bg-accent/45 p-4">{claim.suggested_rewrite ? <><div className="mb-2 flex items-center gap-2 text-sm font-medium"><Sparkles className="h-4 w-4 text-primary" />建议改写</div><Textarea aria-label={`改写 ${claim.claim_id}`} className="min-h-28 bg-background" value={decision?.rewrite_text ?? draft} disabled={Boolean(decision)} onChange={(event) => onDraft(event.target.value)} />{!decision && <div className="mt-3 flex flex-wrap gap-2"><Button size="sm" disabled={busy || draft.trim().length === 0} onClick={onAccept}><CheckCircle2 className="mr-1.5 h-4 w-4" />接受并锁定</Button><Button size="sm" variant="outline" disabled={busy} onClick={onReject}><XCircle className="mr-1.5 h-4 w-4" />不采用</Button></div>}{decision?.status === "accepted" && <p className="mt-3 text-sm text-primary">已加入变更清单，请在页面上方统一预览并生成版本。</p>}{decision?.status === "applied" && <p className="mt-3 text-sm font-medium text-emerald-700">已保留原版本，并创建可回溯的新版本。</p>}{decision?.status === "rejected" && <p className="mt-3 text-sm text-muted-foreground">已记录你的选择，不会修改简历。</p>}</> : <div className="flex min-h-28 items-center text-sm text-muted-foreground"><CheckCircle2 className="mr-2 h-4 w-4 text-emerald-600" />当前主张已有证据支持，无需改写。</div>}</div>
  </CardContent></Card>;
}

function resumeExtractError(error: Error): string {
  if (error instanceof ApiError) {
    if (error.code === "RESUME_FILE_TEXT_EMPTY") return "没有提取到可用文本；扫描版 PDF 暂不支持，请改用可复制文本的文件。";
    if (error.code === "RESUME_FILE_SIZE_INVALID") return "文件不能为空且不能超过 5 MiB。";
    if (error.code === "RESUME_FILE_FORMAT_UNSUPPORTED") return "只支持 PDF、DOCX、TXT 文件，请检查文件格式。";
  }
  return "简历解析失败，请检查文件后重试，或直接粘贴文本。";
}
