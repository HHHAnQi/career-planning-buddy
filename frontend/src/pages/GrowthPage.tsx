import { Link } from "react-router-dom";
import { useInterviews } from "@/api/interviews";
import { usePlans } from "@/api/plans";
import { useReviews } from "@/api/reviews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function GrowthPage(): JSX.Element {
  const plans = usePlans();
  const reviews = useReviews();
  const interviews = useInterviews();
  const completed = (interviews.data?.items ?? []).filter((item) => item.report !== null);
  const comparisons = completed.filter((item) => item.report?.comparison !== null);
  return <div className="mx-auto max-w-4xl space-y-6">
    <header><p className="text-sm font-medium text-primary">诊断进入行动，行动接受复测</p><h1 className="mt-1 text-3xl font-semibold">成长</h1><p className="mt-2 text-sm text-muted-foreground">在同一处查看训练计划、执行复盘和跨场次改善。</p></header>
    <div className="grid gap-4 md:grid-cols-3">
      <Card><CardHeader><CardTitle>训练周期</CardTitle></CardHeader><CardContent><p className="text-3xl font-semibold">{plans.data?.items.length ?? 0}</p><Button asChild variant="link" className="px-0"><Link to="/journey">查看计划</Link></Button></CardContent></Card>
      <Card><CardHeader><CardTitle>执行复盘</CardTitle></CardHeader><CardContent><p className="text-3xl font-semibold">{reviews.data?.items.length ?? 0}</p><Button asChild variant="link" className="px-0"><Link to="/reviews">查看复盘</Link></Button></CardContent></Card>
      <Card><CardHeader><CardTitle>改善比较</CardTitle></CardHeader><CardContent><p className="text-3xl font-semibold">{comparisons.length}</p><p className="text-sm text-muted-foreground">只统计有可追溯跨场次证据的报告</p></CardContent></Card>
    </div>
    <section className="space-y-3"><h2 className="text-xl font-semibold">最近面试发现</h2>{completed.slice(0, 5).map((session) => <Link key={session.interview_id} to={`/interviews/${session.interview_id}/report`}><Card className="mb-3 transition-colors hover:border-primary"><CardContent className="p-5"><strong>{session.report?.weaknesses[0]?.topic ?? "面试报告"}</strong><p className="mt-1 text-sm text-muted-foreground">{session.report?.overall_summary}</p></CardContent></Card></Link>)}</section>
  </div>;
}
