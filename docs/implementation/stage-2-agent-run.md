# Stage 2：Mock Agent Runtime 纵切

## 目标

不接真实模型，用确定性 Mock Provider 跑通完整链路：创建 Run → Runtime/Graph → SSE → 终态结果 → 保存 Plan/Task → 查询恢复。

本阶段不是“先写几个 LangGraph 节点”，而是先把 Agent Runtime 的预算、事件、Trace、Snapshot、取消和终态收敛做完整。

## 必读

- `docs/model-design/agent-runtime/README.md`
- `docs/model-design/agent-nodes/README.md`
- `docs/model-design/api-spec/agent-runs.md`
- `docs/model-design/data-models/trace-tables.md`
- `docs/model-design/state-machines/run-status.mmd`

## 实现范围

### 表

- plans
- tasks
- agent_runs（含 graph/config/input snapshot、result_kind/result payload）
- agent_steps
- tool_calls
- agent_events
- companion_messages

### API

- 内部 `AgentRunService.create` 命令（面向用户的创建只能由 Goal Brief 确认或复盘流程触发）
- GET `/api/v1/agent-runs/{id}`
- GET `/api/v1/agent-runs/{id}/events`
- POST `/api/v1/agent-runs/{id}/cancel`
- GET `/api/v1/plans/active`
- GET `/api/v1/tasks`

### Runtime

必须建立：

```text
AgentRunService
AgentRunExecutor
GraphFactory
NodeRunner
AgentRunFinalizer
BudgetGuard
EventRecorder
TraceRecorder
SnapshotService
```

使用 Mock Provider 实现固定 Graph：

```text
risk_gate → intent_router
  ├─ high → safe_response → degraded
  ├─ missing/unsupported → clarification → degraded
  └─ ready → context_builder → career_planning_agent
             → rule_validator → revise_or_fallback
             → companion_response → persist
```

Stage 2 `available_tools=[]`，不实现检索 Tool；但 tool_calls 表和 Registry 契约可以先建立空实现。

## SSE 规则

- 非 heartbeat 事件先插入 `agent_events`；
- `sequence` 在 Run 内单调递增；
- `Last-Event-ID` 从下一 sequence 继续；
- heartbeat 不落库、不占 sequence；
- terminal event 只能有一个且是最后一个；
- `plan.ready` 与 Plan/Run 终态同事务；
- clarification/navigation/safe_response 也必须能通过 GET Run 恢复结果。

## 可靠 Worker 约束

执行任务可以使用进程内 `asyncio.Task` Registry 作为本地句柄，但可靠性以数据库状态为准：

- dispatcher 通过数据库 lease 抢占 pending Run，attempt fencing 阻止旧执行者写入；
- lease 过期或优雅停机时有界 requeue，超过 deadline/attempt 才 failed；
- 取消接口先写 cancel_requested_at，本地 Task 立即取消，远端 owner 由 heartbeat/节点边界观察；
- finally 通过 Finalizer 保证终态和 terminal event 唯一；
- 进程重启后不从中间节点续跑。

## Mock 场景

至少准备：

1. happy plan；
2. missing profile → clarification；
3. high risk → safe response；
4. invalid candidate → repair 后成功；
5. repair 仍失败 → template degraded；
6. node timeout；
7. user cancel；
8. persist transaction failure。

## 验收

- happy path 产生 1 个带 planning window/weekly_focus 的计划，以及从当天开始连续 7 天、每天 1 个任务；
- completed 必有 result_kind=plan/final_plan_id；
- degraded clarification/safe_response 刷新后仍可恢复；
- SSE 事件顺序稳定，terminal event 唯一；
- 断线后用 Last-Event-ID 续传；
- 重复 Idempotency-Key 不重复创建 Run；
- 同用户并发创建第二个 Run 返回 409；
- Mock invalid schema 只触发一次格式修复；
- Mock 规则失败只触发一次业务 repair；
- Mock timeout/cancel 收敛且没有后续节点继续执行；
- config/input snapshot 已保存且创建后不可修改；
- 用户隔离测试覆盖 Run、Event、Plan 和 Task。
