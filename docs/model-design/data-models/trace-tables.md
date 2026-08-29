# Agent Runtime 表

## agent_runs

| 字段 | 类型 | NULL | 说明 |
|---|---|---:|---|
| id | uuid | NO | PK |
| user_id | uuid | NO | FK users.id |
| idempotency_key | varchar(64) | NO | 与 user_id 联合唯一 |
| request_text | text | NO | 用户原始请求，按敏感数据保护 |
| hint_intent | varchar(32) | YES | create_plan/replan |
| resolved_intent | varchar(32) | YES | create_plan/replan/unsupported |
| replan_mode | varchar(16) | YES | initial/continue/adjust |
| requested_horizon_weeks | smallint | YES | 1..8 |
| goal_type_override | varchar(32) | YES | 本轮显式覆盖 |
| source_plan_id | uuid | YES | replan 来源 |
| source_review_id | uuid | YES | 次日续接/调整来源 Review |
| replay_of_run_id | uuid | YES | Replay 来源 |
| status | varchar(16) | NO | pending/running/completed/degraded/failed/cancelled |
| result_kind | varchar(24) | YES | plan/clarification/safe_response |
| result_payload_json | jsonb | YES | 终态轻量结果，受 Pydantic Schema 约束 |
| final_plan_id | uuid | YES | result_kind=plan 时填写 |
| graph_version | varchar(64) | NO | 固定 Graph 拓扑版本 |
| input_snapshot_json | jsonb | YES | context_builder 后的脱敏输入快照 |
| config_snapshot_json | jsonb | NO | Prompt、模型别名、预算、feature flags |
| model_id | varchar(128) | YES | 主规划模型实际 model id |
| total_tokens_in | integer | NO | default 0 |
| total_tokens_out | integer | NO | default 0 |
| total_cost_cny | numeric(12,6) | NO | default 0 |
| total_latency_ms | integer | NO | default 0 |
| fallback_reason | varchar(64) | YES | 仅 degraded 使用的稳定降级码 |
| error_code | varchar(64) | YES | failed/cancelled 稳定错误码 |
| error_message | varchar(500) | YES | 脱敏内部摘要，普通用户可不返回 |
| risk_category | varchar(32) | YES | |
| deadline_at | timestamptz | NO | |
| cancel_requested_at | timestamptz | YES | 用户发起取消时间 |
| worker_id | varchar(64) | YES | 当前 lease 持有者；仅 running 必填 |
| lease_expires_at | timestamptz | YES | worker 未续租后的接管时间 |
| heartbeat_at | timestamptz | YES | 最近一次 worker 心跳 |
| attempt_count | integer | NO | default 0；每次成功 claim 加一 |
| next_event_sequence | integer | NO | default 1；EventRecorder 原子分配 |
| next_step_sequence | integer | NO | default 1；NodeRunner 原子分配 |
| created_at | timestamptz | NO | |
| started_at | timestamptz | YES | |
| finished_at | timestamptz | YES | |

约束：

- UNIQUE `(user_id, idempotency_key)`；
- partial unique：同用户最多一个 `status IN ('pending','running')`；
- running 必须同时具有 worker_id 和 lease_expires_at；
- claim 使用 `(status, lease_expires_at, created_at)` 索引和 `SKIP LOCKED`；
- completed 必须 `result_kind=plan` 且 `final_plan_id IS NOT NULL`；
- degraded 必须有 result_kind；
- failed/cancelled 不得有 result_kind/final_plan_id，且 error_code 非空；
- completed 的 fallback_reason/error_code 为空；
- degraded 的 fallback_reason 非空、error_code 为空；
- `result_payload_json/input_snapshot_json/config_snapshot_json` 都必须有 Pydantic Schema，不能随意塞字段；
- input/config snapshot 创建后不可修改，Replay 新建独立 Run；
- EventRecorder 使用 `UPDATE agent_runs SET next_event_sequence=next_event_sequence+1 ... RETURNING` 分配序号，不使用 `MAX(sequence)+1`；
- NodeRunner 对 step sequence 使用同样的原子计数方式。

### result_payload_json

- plan：`plan_id/status/plan_date/horizon_end/summary/task_count`；
- clarification：`questions/slot_names/hint_options/reason`；
- safe_response：`message/resource_ids/disclaimer`；
- 不保存完整 Plan、完整网页或未脱敏 Prompt。

## agent_steps

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | PK |
| run_id | uuid | FK agent_runs.id ON DELETE CASCADE |
| sequence | integer | 节点执行顺序 |
| node_name | varchar(64) | 应用枚举校验 |
| attempt | integer | 默认 1 |
| status | varchar(16) | running/completed/failed/skipped |
| prompt_version | varchar(64) nullable | |
| model_id | varchar(128) nullable | |
| tokens_in | integer | |
| tokens_out | integer | |
| cost_cny | numeric(12,6) | |
| latency_ms | integer | |
| input_hash | varchar(64) nullable | 脱敏输入摘要 hash |
| output_hash | varchar(64) nullable | 输出 hash |
| trace_data | jsonb | 脱敏信息 |
| error_code | varchar(64) nullable | |
| error_message | text nullable | 用户不可见内部摘要 |
| created_at | timestamptz | |
| finished_at | timestamptz | |

UNIQUE `(run_id, sequence)`。修复节点使用新的 sequence，`attempt` 仅表示同一节点逻辑尝试次数。

`intent_router` 的 `trace_data` 使用稳定结构：

- `router_version/method/intent/replan_mode`：决策版本、路径与结果；
- `confidence/confidence_band`：版本内决策强度；
- `matched_rule_ids/ambiguity_reasons`：可审计依据和冲突原因；
- `requested_horizon_weeks/missing_slots/requires_fresh_information`：后续 Graph 所需派生量。

Trace 不重复保存用户完整原文。以上均写入既有 JSONB 字段，不新增表或列，因此不需要数据库迁移。

## tool_calls

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | PK |
| run_id | uuid | FK agent_runs.id ON DELETE CASCADE |
| step_id | uuid | FK agent_steps.id ON DELETE CASCADE |
| tool_name | varchar(64) | 白名单 |
| tool_contract_version | varchar(32) | Replay key 的一部分 |
| round | integer | 1..2 |
| args_json | jsonb | 字段级脱敏参数 |
| args_hash | varchar(64) | canonical JSON hash |
| result_json | jsonb nullable | Replay-safe 结构化 fixture，最大 32KB |
| result_preview | text nullable | 开发者 UI 截断摘要 |
| result_hash | varchar(64) nullable | |
| provider | varchar(32) nullable | |
| latency_ms | integer | |
| success | boolean | |
| error_code | varchar(64) nullable | |
| created_at | timestamptz | |

建议索引：`(run_id, tool_name, args_hash)`。同一 Run 相同 Tool+args_hash 可复用成功结果，不重复访问 Provider。

## agent_events

| 字段 | 类型 | 说明 |
|---|---|---|
| id | uuid | PK |
| run_id | uuid | FK agent_runs.id ON DELETE CASCADE |
| sequence | integer | Run 内单调递增，SSE id |
| event_type | varchar(64) | dot notation |
| payload_json | jsonb | 必含 run_id、sequence |
| created_at | timestamptz | |

约束：

- UNIQUE `(run_id, sequence)`；
- 索引 `(run_id, sequence)`；
- heartbeat 不落本表；
- 建议建立 partial unique index：每个 Run 只能有一个 `event_type IN (run.completed, run.degraded, run.failed, run.cancelled)` terminal event；
- terminal event 是该 Run 最后一个持久事件。

SSE 断线续传以本表为事实源，不依赖内存消息队列。
