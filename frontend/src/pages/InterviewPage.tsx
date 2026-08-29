import { Link } from "react-router-dom";
import { useDeleteInterview, useInterviews } from "@/api/interviews";
import { useJobTargets, useResumeVersions } from "@/api/resumes";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function InterviewPage(): JSX.Element {
  const query = useInterviews();
  const resumes = useResumeVersions();
  const targets = useJobTargets();
  const deleteInterview = useDeleteInterview();
  const resumeNames = new Map(resumes.data?.items.map((item) => [item.resume_version_id, item.label]));
  const targetNames = new Map(targets.data?.items.map((item) => [item.job_target_id, `${item.title}${item.company ? ` · ${item.company}` : ""}`]));
  const statusLabel = { draft: "正在生成第一题", active: "继续面试", report_generating: "正在生成报告", completed: "报告已就绪", aborted: "已结束" } as const;
  return <div className="mx-auto max-w-4xl space-y-6">
    <div className="flex items-center justify-between"><div><h1 className="text-2xl font-semibold">面试训练</h1><p className="text-muted-foreground">围绕冻结简历和目标 JD 完成 4–6 题训练。</p></div><Button asChild><Link to="/interviews/new">开始面试</Link></Button></div>
    {query.isLoading && <p role="status" className="text-sm text-muted-foreground">正在加载面试记录…</p>}
    {query.isError && <Card><CardContent className="flex items-center justify-between gap-3 p-5"><p className="text-sm text-destructive">面试记录加载失败。</p><Button variant="outline" onClick={() => void query.refetch()}>重新加载</Button></CardContent></Card>}
    <div className="grid gap-4">{query.data?.items.map((item) => <Card key={item.interview_id}><CardHeader><CardTitle>{targetNames.get(item.job_target_id) ?? (item.interview_type === "role_focused" ? "目标岗位综合" : "简历深挖")}</CardTitle></CardHeader><CardContent className="space-y-3"><p className="text-sm text-muted-foreground">{resumeNames.get(item.resume_version_id) ?? "历史简历版本"} · {new Date(item.created_at).toLocaleDateString("zh-CN")} · {item.asked_question_count} 题</p><div className="flex items-center justify-between gap-3"><span className="text-sm font-medium">{statusLabel[item.status]}</span><div className="flex gap-2"><Button variant="outline" asChild><Link to={item.status === "completed" ? `/interviews/${item.interview_id}/report` : `/interviews/${item.interview_id}`}>{item.status === "completed" ? "查看报告" : "继续"}</Link></Button>{!item.active_run && <Button variant="ghost" disabled={deleteInterview.isPending} onClick={() => window.confirm("永久删除这场面试及其报告？此操作无法撤销。") && deleteInterview.mutate(item.interview_id)}>删除</Button>}</div></div></CardContent></Card>)}</div>
    {query.data?.items.length === 0 && <Card><CardContent className="p-8 text-center text-muted-foreground">还没有面试记录。先准备求职材料，再开始第一场。</CardContent></Card>}
  </div>;
}
