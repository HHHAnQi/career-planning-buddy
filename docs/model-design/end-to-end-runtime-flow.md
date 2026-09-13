# 端到端运行流程

## 1. 首次进入

```text
POST /auth/guest → JWT
GET /me → profile_complete=false
PUT /profile → 建档完成
```

## 2. 创建计划 Run

```text
POST /agent-runs
  → AgentRunService 校验 JWT、Profile、幂等、活动 Run
  → replan 时校验 source_plan 归属
  → 冻结 graph_version + config_snapshot_json
  → insert agent_runs(pending)
  → AgentRunExecutor.submit(run_id)
  → 202 run_id + events_url
```

Agent Run 只支持 create_plan/replan。已有计划查询使用 `/plans` 与 `/tasks`。

## 3. SSE 与执行

```text
GET /agent-runs/{id}/events
  → 回放 agent_events(sequence > Last-Event-ID)
  → 等待新事件

Executor:
  pending→running
  → risk_gate
      ├─ high → safe_response → degraded(safe_response)
      └─ safe → intent_router
          ├─ missing/unsupported → clarification → degraded(clarification)
          └─ ready → context_builder
              → freeze input_snapshot_json
              → career_planning_agent
              → rule_validator
                  ├─ pass
                  └─ fail → revise_or_fallback once → revalidate/fallback
              → companion_response
              → persist
              → completed(plan) / degraded(template plan)
```

每个节点统一经过 NodeRunner：

1. 检查取消、预算和 deadline；
2. 写 agent_steps running；
3. 写 node.started；
4. 执行并记录模型/Tool/耗时；
5. 更新 step；
6. 写 node.completed；
7. 未知异常交给 Executor/Finalizer 收敛，不由节点吞掉。

## 4. CareerPlanningAgent

- Stage 2/3 Tool 列表为空；
- Stage 4 可调用 memory_lookup/rag_retrieve/web_search；
- Stage 2/3 AgentTurn 最多 1 次；Stage 4 最多 3 次、2 轮 Tool、4 个 Tool；
- Tool 结果先持久化、清洗和截断，再作为不可信 evidence 回填；
- 输出必须是 PlanCandidate：中期方向/weekly_focus + planning_date 当天行动批次；
- 格式错误最多修复一次；
- 业务规则错误由专用 repair Prompt 修复一次，修复时关闭 Tool。

## 5. Persist 事务

```text
lock active plan + verify run running/not cancelled
+ archive old active plan first inside transaction if replan
+ create plan(generated, parent_plan_id)
+ create rolling seven-day tasks
+ create companion_message
+ validate and store evidence_refs_json
+ set run.final_plan_id/result_kind/result_payload/status
+ write plan.ready
+ write unique terminal event
COMMIT
```

事件与业务终态必须同事务或具备可靠 outbox 语义。MVP 使用 `agent_events` 作为轻量 outbox。

## 6. 其他终态分支

### Clarification

```text
result_kind=clarification
+ result_payload_json(questions/slots/options)
+ clarification.requested
+ run.degraded
COMMIT
```

### Safe Response

```text
result_kind=safe_response
+ reviewed result_payload_json
+ run.degraded
COMMIT
```

failed/cancelled 不产生 result_kind。每个 Run 只能有一个 terminal event。

## 7. 任务执行

- GET `/tasks?date=today`；
- PATCH pending→in_progress，同时 plan generated→active；
- PATCH in_progress→completed；
- 全部完成时 plan→completed；
- 放弃记录原因；
- 过期由定时任务处理。

## 8. 复盘和重规划

```text
POST /reviews
  → 读取任务事实
  → 计算完成/放弃数量
  → 规则判断 suggested_replan + next_plan_action(continue/adjust)
  → 保存 review + companion

POST /reviews/{id}/start-next-plan
  → 用户确认后 create Agent Run(replan, source_plan_id, source_review_id)
  → context_builder 读取 planning window/completed facts/blockers
  → Agent 生成中期方向 + 从下一 planning_date 开始的七天执行表
  → 归档来源计划与创建新计划在同一事务提交
```

## 9. 记忆和证据

Stage 4：context_builder 加载少量 pinned memories；Agent 按需调用 Memory/RAG/Search Tool。敏感候选由用户确认后才进入 memories。成功 Run 后可 best-effort distill evidence，不阻塞 Plan。

## 10. 故障收敛

| 故障 | 结果 |
|---|---|
| Profile 缺失 | clarification + degraded |
| High risk | safe_response + degraded |
| LLM 格式错误 | 格式修复一次，仍失败按 fallback/failed |
| 业务规则失败 | repair 一次，仍失败模板 degraded |
| Provider/Tool 超时 | 按剩余上下文决定继续、degraded 或 failed |
| Run 截止时间 | failed + AGENT_DEADLINE_EXCEEDED |
| 用户取消 | cancel_requested_at + cancelled |
| 进程重启/lease 过期 | running → run.requeued → pending → 新 worker claim |
| 重试耗尽 | failed + AGENT_RETRY_EXHAUSTED |
| SSE 断线 | 依据 agent_events 重放 |

## 11. 权威恢复

前端刷新后不依赖内存：

- GET `/me`；
- GET `/agent-runs/{id}`，读取 result_kind/result；
- GET `/plans/active`；
- GET `/tasks`。

SSE 只负责实时体验，不是唯一事实源。
