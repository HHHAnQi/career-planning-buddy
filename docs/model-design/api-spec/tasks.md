# Tasks API

## GET /api/v1/tasks

Query：

| 参数 | 默认 | 说明 |
|---|---|---|
| date | today | scheduled_date |
| state | null | 状态过滤 |
| plan_id | null | 计划过滤 |
| cursor | null | |
| limit | 50 | 1~100 |

## PATCH /api/v1/tasks/{task_id}

### Request

```json
{
  "state": "completed",
  "version": 3,
  "actual_minutes": 42,
  "abandoned_reason": null,
  "abandoned_reason_text": null
}
```

规则：

- pending→in_progress；
- in_progress→completed；
- completed→in_progress，用于用户撤销误操作；撤销时清空 completed_at 和 actual_minutes，并把刚完成的 Plan 恢复为 active；
- pending/in_progress→abandoned；
- expired 只能由系统 Job 设置；客户端不能主动写 expired；
- completed 需要 actual_minutes；
- abandoned 需要 abandoned_reason；other 时还需 reason_text；
- 首个任务开始时，同事务把计划 generated→active 并写 adopted_at。
- 任务完成只更新它所属的 `scheduled_date`，不会删除或补位；Plan 在当前周期任务全部进入 completed/abandoned/expired 后进入 completed。

## 每日详情与调整

- `GET /tasks/{task_id}`：返回任务、本周重点、成功标准和是否可编辑；
- `PATCH /tasks/{task_id}/details`：用户直接编辑 pending/in_progress Task 的内容与预计时间；
- `POST /tasks/{task_id}/adjustment-proposals`：让 AI 生成结构化差异提案，不修改 Task；
- `POST /task-adjustment-proposals/{id}/confirm|reject`：用户确认后才原子应用或拒绝；
- 调整不得突破每日时间预算或固定周边界。completed/abandoned/expired 属于已结算状态，禁止人工或 AI 修改；completed 必须先由用户明确撤销为 in_progress，之后才恢复编辑。

### Response 200

```json
{
  "task": {},
  "plan_status": "active",
  "companion_message": "你已经完成了第一步。"
}
```

version 冲突或非法状态转移返回 409。

## PATCH /api/v1/tasks/{task_id}/checklist

执行步骤与验收标准是两层不同事实：步骤记录“做了什么”，验收记录“成果是否满足完成条件”。清单接口只修改执行步骤，不允许提前确认验收结果。

切换一个执行步骤：

```json
{
  "version": 3,
  "step_index": 0,
  "step_completed": true
}
```

规则：

- 每次只修改一个步骤，并使用 Task `version` 做并发控制；
- 首次勾选会将 pending Task 置为 in_progress；
- 全部步骤完成后，`completion_ready=true`、`verification_status=ready`，客户端才启用验收入口；
- 已完成 Task 取消任一步骤时，恢复为 in_progress，清空实际用时并使原验收失效；
- 修改 `starter_action` 会清空步骤进度，修改 `deliverable` 会使原验收失效；
- 当前固定周期内因历史兼容处于 archived 的 Plan 仍允许执行和验收，避免“能展示、不能操作”；
- 计划生成约束要求最后一个执行步骤明确形成或核验 `deliverable`，避免步骤与验收标准脱节。

## PATCH /api/v1/tasks/{task_id}/verification

验收未通过：

```json
{
  "version": 4,
  "passed": false
}
```

验收通过并完成任务：

```json
{
  "version": 5,
  "passed": true,
  "actual_minutes": 42
}
```

规则：

- 后端再次校验所有执行步骤均完成，不能只依赖前端按钮门禁；
- 未通过时保留步骤勾选，Task 保持 in_progress，`verification_status=failed`；
- 通过时在一个事务中写入 `passed`、实际用时和 completed，避免中间状态；
- 验收状态为 `not_ready → ready → failed|passed`；failed 可以再次验收；
- 用户撤销完成后，步骤保留，但验收恢复为 ready，需要重新验收。

任务级 `rationale` 仍用于模型生成、规则校验和审计，但“今天”卡片不再展示“为什么现在做”，避免不可操作的泛化文案干扰执行。
