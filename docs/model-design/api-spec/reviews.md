# Reviews API

## POST /api/v1/reviews

需要 `Idempotency-Key`。

```json
{
  "plan_id": "c91f8734-2839-4f55-9db1-1c39b8a410f2",
  "review_date": "2026-07-31",
  "mood": 2,
  "blockers": "部署环境一直报错",
  "adjustment_request": "明天任务少一点",
  "free_text": "今天把接口跑通了，但没有完成部署"
}
```

客户端不提交 completed_task_ids 和 abandoned_task_ids。Service 根据该日期的 tasks 计算数量，避免事实冲突。

### Response 201

```json
{
  "review_id": "71f6b355-d6a8-480c-890d-684d8bcb2785",
  "completed_count": 1,
  "abandoned_count": 1,
  "suggested_replan": true,
  "replan_reason": "时间预算下降且存在持续阻塞",
  "next_plan_action": "adjust",
  "companion_message": "今天已经推进了接口，明天先把部署拆小。"
}
```

`next_plan_action`：

- `continue`：方向不变，在当前固定七天周期结算后生成下一 planning_date 的行动批次；
- `adjust`：根据 blocker/时间/用户指令调整周重点和下一批任务。

## GET /api/v1/reviews

Query：plan_id、date_from、date_to、cursor、limit。

## GET /api/v1/reviews/{review_id}

返回一条属于当前用户的完整复盘。

## PATCH /api/v1/reviews/{review_id}

通过 `version` 乐观锁修改 mood、blockers、adjustment_request、free_text；字段传 null 表示清空。修改后服务端重新计算 suggested_replan、replan_reason 和陪伴反馈，任务完成/放弃数量仍从数据库事实读取。

## DELETE /api/v1/reviews/{review_id}

删除尚未用于下一计划的复盘并返回 204。已经存在 `next_plan_run_id` 的复盘是后续计划的输入事实，修改或删除均返回 409 `STATE_REVIEW_ALREADY_CONSUMED`。

## POST /api/v1/reviews/{review_id}/start-next-plan

需要 `Idempotency-Key`。每个 Review 最多成功创建一个 next Plan Run。

这个端点不要求 `suggested_replan=true`：

- false 时创建 `hint_intent=replan, replan_mode=continue`；
- true 或用户有 adjustment_request 时创建 `replan_mode=adjust`；
- source_plan_id 固定使用 review.plan_id，source_review_id 使用当前 review；planning_date 沿固定周边界推进，并且必须落在用户 start_date/deadline 内；即使本周期任务提前全部完成，也必须等待周期结束，不能提前创建未来周期；
- 只有用户点击后才创建，不后台静默生成。

Response 202：

```json
{
  "run_id": "...",
  "status": "pending",
  "replan_mode": "adjust",
  "events_url": "/api/v1/agent-runs/.../events"
}
```

该端点不立即归档原计划。只有“归档来源计划 + 插入新计划 + Run 终态”的事务整体成功后，来源计划才变为 archived；新 Run 失败时原计划保持原状态。
