import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCreateInterview } from "@/api/interviews";
import { useJobTargets, useResumeVersions } from "@/api/resumes";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function InterviewSetupPage(): JSX.Element {
  const resumes = useResumeVersions(); const targets = useJobTargets(); const create = useCreateInterview(); const navigate = useNavigate();
  const [resumeId, setResumeId] = useState(""); const [targetId, setTargetId] = useState(""); const [type, setType] = useState("role_focused");
  useEffect(() => { if (!resumeId && resumes.data?.items[0]) setResumeId(resumes.data.items[0].resume_version_id); }, [resumeId, resumes.data]);
  useEffect(() => { if (!targetId && targets.data?.items[0]) setTargetId(targets.data.items[0].job_target_id); }, [targetId, targets.data]);
  const submit = (event: FormEvent) => { event.preventDefault(); create.mutate({ resume_version_id: resumeId, job_target_id: targetId, interview_type: type, question_limit: 4, followup_limit: 2 }, { onSuccess: (value) => navigate(`/interviews/${value.interview_id}?run_id=${value.run_id}`, { replace: true }) }); };
  return <Card className="mx-auto max-w-2xl"><CardHeader><CardTitle>开始一场面试</CardTitle><CardDescription>开始后材料版本冻结；生成问题不需要额外确认。</CardDescription></CardHeader><CardContent><form className="space-y-5" onSubmit={submit}>
    <label className="grid gap-2 text-sm font-medium">简历版本<select className="h-11 rounded-md border bg-background px-3" value={resumeId} onChange={(e) => setResumeId(e.target.value)} required><option value="">请选择</option>{resumes.data?.items.map((x) => <option key={x.resume_version_id} value={x.resume_version_id}>{x.label}</option>)}</select></label>
    <label className="grid gap-2 text-sm font-medium">目标岗位<select className="h-11 rounded-md border bg-background px-3" value={targetId} onChange={(e) => setTargetId(e.target.value)} required><option value="">请选择</option>{targets.data?.items.map((x) => <option key={x.job_target_id} value={x.job_target_id}>{x.title}</option>)}</select></label>
    <label className="grid gap-2 text-sm font-medium">面试类型<select className="h-11 rounded-md border bg-background px-3" value={type} onChange={(e) => setType(e.target.value)}><option value="role_focused">目标岗位综合</option><option value="resume_deep_dive">简历深挖</option></select></label>
    {(resumes.data?.items.length === 0 || targets.data?.items.length === 0) && <p className="text-sm text-destructive">请先在“求职材料”保存简历和 JD。<Link className="ml-2 underline" to="/materials?next=/interviews/new">现在去准备</Link></p>}
    {(resumes.isError || targets.isError || create.isError) && <p className="text-sm text-destructive">材料或面试创建失败，请重试。</p>}
    <Button disabled={create.isPending || !resumeId || !targetId || resumes.isLoading || targets.isLoading}>开始并生成第一题</Button>{create.isPending && <span role="status" className="ml-3 text-sm text-muted-foreground">正在创建面试…</span>}
  </form></CardContent></Card>;
}
