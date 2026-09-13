# context_builder — 上下文构建

## 定位

确定性上下文节点。负责把数据库事实转换成受预算约束、可序列化、可回放的
`PlanningContext`。它不调用主模型、不执行 Web Search、不创建业务实体；Stage 6A
仅通过 Repository best-effort 更新被选中 Memory 的 `last_used_at` 使用元数据。

## Input

```python
class ContextBuilderInput(BaseModel):
    run_id: UUID
    user_id: UUID
    intent: Literal["create_plan", "replan"]
    replan_mode: Literal["initial", "continue", "adjust"]
    effective_goal_type: GoalType
    source_plan_id: UUID | None
    requested_horizon_weeks: int | None
```

## Output

```python
class PlanningWindow(BaseModel):
    planning_date: date
    horizon_start: date
    horizon_end: date
    horizon_weeks: int = Field(ge=1, le=8)

class PlanningContext(BaseModel):
    profile: ProfileContext
    planning_window: PlanningWindow
    active_plan: PlanContext | None
    source_plan: PlanContext | None
    source_review: ReviewContext | None
    recent_tasks: list[TaskContext]
    recent_reviews: list[ReviewContext]
    pinned_memories: list[MemoryContext]
    completed_facts: list[CompletedFact]
    blockers: list[str]
    task_history_summary: str | None
    review_history_summary: str | None
    timezone: str
    time_budget_minutes: int
    token_estimate: int
```

## 规划窗口

- 首次规划或用户手动即时调整时，`planning_date` 使用用户时区的本地日期；
- 从 Review 执行 `start-next-plan` 时，`planning_date=max(用户本地今日, source_plan.plan_date + 7 天)`；日期始终按固定周边界推进，不按复盘日滚动补位；
- `profile.start_date` 与 `profile.deadline` 必填并形成用户确认的闭区间，最长为 8 周；用户请求中的周数只作意图参考，不能覆盖日期边界；
- 首次 `planning_date=max(用户本地今日, profile.start_date)`；续接也不得早于 start_date；
- `horizon_start=planning_date`，`horizon_end=profile.deadline` 并冻结到 input snapshot；
- 若最终周期距离 `horizon_end` 少于 7 天，只生成剩余 1~7 天；`planning_date > deadline` 时拒绝生成；
- Agent 不得自行扩大或缩短窗口。

## 数据来源与上限

| 数据 | 上限 | 规则 |
|---|---:|---|
| Profile | 1 | 必须是当前用户最新 version |
| Active/Source Plan | 各 1 | replan 的 source_plan 必须属于当前用户，可为 generated/active/completed |
| Source Review | 1 | 从 `/reviews/{id}/start-next-plan` 创建时注入 |
| Recent Tasks | 查询 30，完整保留最近 5 | 更早记录做确定性摘要 |
| Recent Reviews | 查询 7，完整保留最近 2 | 更早记录只聚合重复 blocker/adjustment |
| Selected Memories | 最多 5 / 1200 字符 | active、当前用户；pinned 优先，再做语义+时间衰减排序 |
| Completed Facts | 最多 20 | 从任务事实确定性生成 |
| Blockers | 最多 10 | 从 abandoned/review 结构化字段提取 |

Stage 6A 自动检索与本次请求相关的长期 Memory；更广泛的 RAG/Search 仍由 Agent
通过只读 Tool 按需获取。未确认 MemoryCandidate 永远不属于检索数据源。

## 预算和裁剪顺序

Profile > planning window > source/active plan > completed facts > 当前未完成任务 > source/recent review > blockers > pinned memories。

超预算时：

1. 删除低优先级和重复项；
2. 对历史文本做确定性字段投影，不调用 LLM；
3. 仍超预算则只保留最近和最相关记录；
4. 记录 dropped counts 和 token_estimate 到 Trace。

## 快照

节点完成后由 SnapshotService 幂等写入 `agent_runs.input_snapshot_json`。快照存 id/version、planning window 和必要字段，不保存 ORM 对象、无关隐私和 Provider 凭据。Replay 使用快照，不重新读取用户当前画像。

## 不变量

- 所有查询按 user_id 过滤；
- 除选中 Memory 的 `last_used_at` 外不更新业务字段；该更新失败不得阻塞 Run；
- 不把高风险原文写入上下文快照；
- 不把搜索结果或模型输出伪装成数据库事实；
- replan 必须包含 source_plan、completed_facts 与 blockers；
- continue 模式优先使用最近完成 Plan，adjust 模式优先使用触发调整的 Review。

## 测试

- 用户隔离；
- create_plan/continue/adjust 三种上下文；
- source_plan 越权；
- deadline 精确收口、最终短周期与 8 周上限；
- Review 触发 next plan 时 planning_date 按 source plan 的固定第 8 天推进；
- 预算裁剪稳定且确定；
- 快照在原数据改变后仍不变；
- 高风险敏感字段不进入 snapshot。
