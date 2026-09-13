import { CalendarCheck2, FileText, LayoutDashboard, Map, MessageSquareText, Sparkles, UserRound } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { cn } from "@/lib/utils";

const NAV_ITEMS: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/workspace", label: "工作台", icon: LayoutDashboard },
  { to: "/today", label: "今日计划", icon: CalendarCheck2 },
  { to: "/journey", label: "路线", icon: Map },
  { to: "/materials", label: "材料", icon: FileText },
  { to: "/interviews", label: "面试", icon: MessageSquareText },
  { to: "/me", label: "我的", icon: UserRound },
];

function Navigation({ mobile = false }: { mobile?: boolean }): JSX.Element {
  return <nav aria-label="主导航" className={mobile ? "grid grid-cols-6" : "flex flex-col gap-1"}>
    {NAV_ITEMS.map((item) => { const Icon = item.icon; return <NavLink key={item.to} to={item.to} className={({ isActive }) => cn("flex min-h-11 items-center justify-center gap-2 rounded-xl text-sm font-medium transition-colors", mobile ? "flex-col gap-0.5 rounded-none py-2 text-[11px]" : "justify-start px-3", isActive ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/70 hover:text-foreground")}><Icon className="h-5 w-5" aria-hidden="true" /><span>{item.label}</span></NavLink>; })}
  </nav>;
}

export function AppLayout(): JSX.Element {
  return <div className="min-h-screen bg-background md:grid md:grid-cols-[224px_minmax(0,1fr)]">
    <aside className="hidden min-h-screen border-r bg-card/90 p-4 md:sticky md:top-0 md:flex md:h-screen md:flex-col">
      <div className="mb-8 flex items-center gap-2 px-2 pt-2"><span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground"><Sparkles className="h-5 w-5" /></span><div><div className="font-semibold tracking-tight">求职搭子</div><div className="text-xs text-muted-foreground">证据化 AI 求职 Agent</div></div></div>
      <Navigation /><p className="mt-auto px-3 text-xs leading-5 text-muted-foreground">理解目标、组织上下文，把面试反馈回流成下一步行动。</p>
    </aside>
    <div className="min-w-0"><header className="sticky top-0 z-30 border-b bg-background/95 backdrop-blur md:hidden"><div className="flex h-14 items-center gap-2 px-4"><Sparkles className="h-5 w-5 text-primary" /><span className="font-semibold tracking-tight">求职搭子</span></div></header><main className="min-w-0 px-4 pb-28 pt-6 sm:px-6 md:px-8 md:pb-10 md:pt-8"><Outlet /></main></div>
    <div className="fixed inset-x-0 bottom-0 z-40 border-t bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"><Navigation mobile /></div>
  </div>;
}
