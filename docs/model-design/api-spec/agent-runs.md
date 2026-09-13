# Agent Run API

面向用户的计划创建入口必须先使用 `/api/v1/goal-briefs` 完成澄清与确认；确认接口才会创建本页定义的 Agent Run。HTTP API 不提供直接创建 Agent Run 的端点。复盘续接等受控流程调用内部 `AgentRunService.create` 命令，并自行承担明确的用户确认语义。

## 内部 Agent Run 创建命令

创建规划或重规划 Run。它不是公开 HTTP 接口，只能由 Goal Brief 确认事务或受控复盘流程调用。

### 命令输入

```json
{
  "message": "帮我制定未来五周的大模型应用开发秋招计划",
  "hint_intent": "create_plan",
  "goal_type_override": null,
  "source_plan_id": null
}
```

字段：

| 字段 | 类型 | 约束 |
|---|---|---|
| message | string | 1~2000 |
| hint_intent | create_plan/replan/null | 弱提示；不能覆盖消息语义、来源计划或服务端 Review 决策 |
| goal_type_override | GoalType/null | 仅用户明确表达目标变化时使用 |
| source_plan_id | UUID/null | replan 可显式指定；省略时优先当前 generated/active，否则最近 completed Plan |

已有计划查询仍由 `/plans` 与 `/tasks` 提供数据；如果用户在 Agent 入口发出查询型消息，
Run 返回结构化页面导航，不调用规划模型，也不创建新 Plan。

意图路由不依赖 LLM。只有消息包含受支持的规划语义并满足来源约束时才进入生成 Graph；
仅有 `hint_intent`、问候或含糊文本返回带下一步动作的 `intent_uncertain`；查询类文本返回
`navigation`。两类结果都不会调用规划模型。服务端 Review 强制的 Replan 优先级高于消息中的
查询词，避免工作流决策被自然语言表面特征覆盖。

### 创建前校验

1. JWT 用户有效；
2. Idempotency-Key 合法；
3. source_plan_id 提供时必须属于当前用户；可作为来源的状态为 generated/active/completed，archived 仅允许显式指定；显式 hint=replan 但没有可用来源计划时返回 422；
4. 同用户没有 pending/running Run；
5. 生成并冻结 `graph_version/config_snapshot_json`；
6. 创建 `agent_runs(pending)` 后提交执行器。

### 创建结果

```json
{
  "run_id": "f880d3e2-2de7-48aa-b123-068d1d6f5e69",
  "status": "pending",
  "events_url": "/api/v1/agent-runs/f880d3e2-2de7-48aa-b123-068d1d6f5e69/events"
}
```

重复 `(user_id, Idempotency-Key)` 返回原 Run；同用户已有活动 Run 返回冲突。普通客户端向 `POST /api/v1/agent-runs` 发起请求时返回 404，不能通过拼装请求绕过确认门禁。

## GET /api/v1/agent-runs/{run_id}

返回权威状态。普通用户只看到用户可理解的终态结果，不返回完整 Prompt、Tool 参数和内部 Trace。

### Plan 结果

```json
{
  "run_id": "...",
  "status": "completed",
  "user_status": "ready",
  "status_message": "新的职业计划已经准备好。",
  "resolved_intent": "create_plan",
  "replan_mode": "initial",
  "result_kind": "plan",
  "result": {
    "plan_id": "...",
    "status": "generated",
    "plan_date": "2026-07-31",
    "horizon_end": "2026-09-03",
    "summary": "今天先补齐可演示闭环",
    "task_count": 7
  },
  "final_plan_id": "...",
  "fallback_reason": null,
  "error_code": null,
  "risk_category": null,
  "total_tokens_in": 1200,
  "total_tokens_out": 530,
  "total_cost_cny": "0.013200",
  "total_latency_ms": 8120,
  "created_at": "...",
  "finished_at": "...",
  "cancel_requested_at": null
}
```

### Clarification 结果

```json
{
  "run_id": "...",
  "status": "degraded",
  "result_kind": "clarification",
  "result": {
    "message": "完善职业画像后，我才能生成更适合你的行动计划。",
    "questions": ["你目前处于哪个求职阶段？"],
    "slot_names": ["stage"],
    "hint_options": {"stage": ["exploring", "preparing", "applying", "interviewing"]},
    "reason": "profile_incomplete",
    "suggested_actions": [{
      "action": "complete_profile",
      "label": "完善职业资料",
      "target_route": "/settings/profile"
    }],
    "target_route": "/settings/profile"
  },
  "final_plan_id": null,
  "fallback_reason": "profile_incomplete"
}
```

`reason` 的稳定取值：

- `profile_incomplete`：意图已确定，但生成所需 Profile 字段缺失；
- `intent_uncertain`：消息语义不足、hint 冲突或重规划缺少来源；
- `unsupported_intent`：保留给无法由当前产品能力处理的非生成请求；计划/任务查询已改为 Navigation。

### Navigation 结果

```json
{
  "run_id": "...",
  "status": "degraded",
  "user_status": "action_required",
  "status_message": "可以直接前往对应页面继续。",
  "resolved_intent": "navigate",
  "result_kind": "navigation",
  "result": {
    "action": "view_today_tasks",
    "label": "查看今日任务",
    "target_route": "/today",
    "message": "这个请求不需要重新生成计划，可以直接查看今天的任务。"
  },
  "final_plan_id": null,
  "fallback_reason": "resource_navigation"
}
```

这里的数据库状态仍为 `degraded`，表示 Graph 没有生成 Plan；产品界面必须使用
`user_status=action_required`，不能向用户展示“降级”术语。

### Safe Response 结果

```json
{
  "run_id": "...",
  "status": "degraded",
  "result_kind": "safe_response",
  "result": {
    "message": "...",
    "resource_ids": ["default-local-resource"],
    "disclaimer": "..."
  },
  "final_plan_id": null,
  "fallback_reason": "high_risk_routed"
}
```

`start-next-plan` 创建的 Run 由服务端额外注入 `source_review_id` 和 `replan_mode=continue/adjust`，客户端不能伪造。

failed/cancelled 时 `result_kind/result/final_plan_id` 均为空，并返回稳定 `error_code`；`fallback_reason` 仅用于 degraded。

## GET /api/v1/agent-runs/{run_id}/events

SSE，支持 `Last-Event-ID`。SSE `id` 等于 `agent_events.sequence`。

事件：

- run.created
- node.started
- node.completed
- tool.called
- tool.returned
- progress
- clarification.requested
- companion.message
- plan.ready
- run.degraded
- run.failed
- run.cancelled
- run.completed
- heartbeat

示例：

```text
id: 7
event: progress
data: {"run_id":"...","sequence":7,"stage":"validating","message":"正在检查任务时长"}
```

### 事件规则

- 除 heartbeat 外，事件先持久化再发送；
- heartbeat 不占 sequence；
- terminal event 是最后一个持久事件；
- 同一 Run 只允许一个 terminal event；
- `plan.ready` 只在 Plan 事务成功后出现；
- `run.degraded` payload 必含 result_kind 与 fallback_reason。

### 重连

- 无 Last-Event-ID：从 sequence 1 开始；
- 有 Last-Event-ID：从 `last + 1` 开始；
- 先回放数据库历史，再等待新事件；
- Run 已终态且历史发送完后关闭连接；
- 前端仍应调用 GET Run 获取权威结果。

## POST /api/v1/agent-runs/{run_id}/cancel

取消 pending/running Run。需要 `Idempotency-Key`。

```json
{"reason":"user_abort"}
```

服务端先写 `cancel_requested_at`，再尝试取消本进程 Task。接口只表示“取消请求已接受”，不能提前声称 Run 已进入 cancelled。

如果 API 请求与 Run owner 不在同一进程，owner heartbeat 会读取取消标记并取消正在执行的
Task；NodeRunner 在进入下一节点前再次查询数据库，作为跨进程传播兜底。

Response 202：

```json
{
  "run_id": "...",
  "status": "running",
  "cancel_requested": true
}
```

客户端随后通过 GET Run/SSE 等待权威 `cancelled` 终态。重复请求取消中的 Run 返回相同语义；已经 cancelled 可返回 200 当前结果；completed/degraded/failed 再取消返回 409。取消请求最终必须由 Finalizer 写 `run.cancelled`，不能只更新内存 Task。

## 主要错误

| HTTP | code |
|---:|---|
| 404 | NOT_FOUND_RUN |
| 404 | NOT_FOUND_SOURCE_PLAN |
| 409 | STATE_RUN_ALREADY_ACTIVE |
| 409 | STATE_RUN_ALREADY_FINISHED |
| 422 | VALIDATION_RUN_INVALID |
| 422 | VALIDATION_REPLAN_SOURCE_UNAVAILABLE |
| 429 | RATE_LIMITED |
