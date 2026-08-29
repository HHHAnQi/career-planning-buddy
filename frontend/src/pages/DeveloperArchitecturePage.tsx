import { ArrowRight, BrainCircuit, CheckCircle2, Database, Gauge, GitBranch, ShieldCheck, Wrench } from "lucide-react";
import { Link } from "react-router-dom";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const capabilities = [
  { icon: BrainCircuit, title: "受控 Agent Runtime", description: "单 CareerPlanningAgent 负责候选决策，风险、路由、校验、持久化由确定性节点约束。", proof: "节点 Trace、Graph/Config Snapshot、唯一终态" },
  { icon: Wrench, title: "自主工具调用", description: "模型只可调用显式白名单的 Memory、RAG、Web Search；参数校验、超时、预算和结果复用统一治理。", proof: "ToolCall、args hash、fixture、evidence ref" },
  { icon: Database, title: "上下文与记忆", description: "画像、路线、任务、复盘、材料与面试证据按场景选择，并在 Run 中冻结输入快照。", proof: "Memory/RAG、版本引用、快照 SHA-256" },
  { icon: ShieldCheck, title: "人机协作边界", description: "Agent 生成候选；简历改写、目标确认等高影响动作由用户确认，且保留原版本与审计记录。", proof: "accepted/rejected/applied、parent version" },
  { icon: Gauge, title: "评测与可观测性", description: "固定数据集、规则 Grader、成本与延迟指标、Bad Case 和校准状态形成离线质量门。", proof: "Experiment → Trial → Grade → Report" },
  { icon: GitBranch, title: "失败恢复", description: "预算、超时、取消、lease 接管、fallback 和唯一 Finalizer 避免 Agent 在异常后留下半完成状态。", proof: "error code、fallback reason、terminal invariant" },
];

export function DeveloperArchitecturePage(): JSX.Element {
  return <div className="mx-auto max-w-6xl space-y-7">
    <header className="rounded-3xl border bg-gradient-to-br from-primary/10 via-card to-accent/50 p-7"><Badge variant="secondary">Architecture Map</Badge><h1 className="mt-3 text-3xl font-semibold">求职场景 AI Agent 技术路线</h1><p className="mt-3 max-w-3xl leading-7 text-muted-foreground">产品不是把对话框包装成 Agent，而是把材料、规划、执行、面试和复盘纳入一个可控制、可追踪、可评测的人机协作系统。</p><div className="mt-5 flex gap-3"><Button asChild><Link to="/dev/runs">查看真实 Run<ArrowRight className="ml-2 h-4 w-4" /></Link></Button><Button asChild variant="outline"><Link to="/dev/evals">查看 Eval</Link></Button></div></header>

    <Card><CardHeader><CardTitle>用户价值链路与工程证据一一对应</CardTitle></CardHeader><CardContent><div className="grid gap-3 md:grid-cols-4">{["材料与目标输入", "Agent 选择上下文并决策", "用户确认并执行", "复盘沉淀为下一轮上下文"].map((label, index) => <div className="relative rounded-2xl border bg-background p-4" key={label}><span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">{index + 1}</span><p className="mt-3 font-medium">{label}</p>{index < 3 && <ArrowRight className="absolute -right-5 top-1/2 z-10 hidden h-5 w-5 text-primary md:block" />}</div>)}</div></CardContent></Card>

    <section className="grid gap-4 md:grid-cols-2">{capabilities.map(({ icon: Icon, title, description, proof }) => <Card key={title}><CardContent className="p-5"><div className="flex items-start gap-4"><span className="rounded-2xl bg-primary/10 p-3 text-primary"><Icon className="h-5 w-5" /></span><div><h2 className="font-semibold">{title}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p><p className="mt-3 flex items-center gap-2 text-xs"><CheckCircle2 className="h-3.5 w-3.5 text-primary" />可验证：{proof}</p></div></div></CardContent></Card>)}</section>

    <Card className="border-amber-300/60"><CardHeader><CardTitle className="text-base">当前能力边界</CardTitle></CardHeader><CardContent className="space-y-2 text-sm leading-6 text-muted-foreground"><p>系统是单 Agent + 受控工作流，不宣称多 Agent、MCP 市场或微服务。</p><p>开发者页现有 Replay 接口是明确标记的 legacy trace clone；确定性 V2 Replay 仍以输入/配置快照和 Tool fixture 真实重执行为验收目标。</p><p>简历主张核验是证据边界内的辅助判断，不等同于背景调查；证据不足不会被表述为事实错误。</p></CardContent></Card>
  </div>;
}
