# plans — 求职计划版本

一个 Plan 是“中期方向 + 从当前 planning_date 开始的固定行动周期”。通常周期为 7 天；最终周期若在目标日期前不足 7 天，则只包含剩余 1~7 天。周期内完成任务只更新状态，不删除任务、不自动补入第八天；周期到期或全部结算后才创建下一版本。

| 字段 | 类型 | NULL | 默认/约束 | 说明 |
|---|---|---:|---|---|
| id | uuid | NO | PK | |
| user_id | uuid | NO | FK users.id | 所属用户 |
| source_run_id | uuid | NO | UNIQUE, FK agent_runs.id | 产生该计划的 Run |
| parent_plan_id | uuid | YES | FK plans.id | continue/adjust 的上一个计划 |
| status | varchar(16) | NO | generated/active/completed/archived | 见状态机 |
| plan_date | date | NO | | 当前固定周期起始日期，按用户时区确定 |
| horizon_start | date | NO | | 方向层起始日期 |
| horizon_end | date | NO | | 等于用户目标日期，且最多展开 8 周 |
| overall_direction | varchar(500) | NO | | 中期整体方向 |
| weekly_focus_json | jsonb | NO | default '[]' | 1~8 条 `{week_index,focus,success_signal}` |
| summary | varchar(500) | NO | | 当前版本用户可读摘要 |
| rationale | text | NO | | 规划理由 |
| adjustment_reason | text | YES | | adjust replan 必填，continue 可为空 |
| assumptions_json | jsonb | NO | default '[]' | 最多 5 条假设 |
| evidence_refs_json | jsonb | NO | default '[]' | `[{kind,id}]`，Pydantic 校验 |
| metadata_json | jsonb | NO | default '{}' | prompt/model/replan_mode 等非核心元数据 |
| version | integer | NO | default 1 | 乐观锁 |
| adopted_at | timestamptz | YES | | 首个任务开始时写入 |
| completed_at | timestamptz | YES | | 当前周期任务全部进入终态时写入；撤销完成任务时清空 |
| archived_at | timestamptz | YES | | 被下一版本替代或手动归档 |
| created_at | timestamptz | NO | now() | |
| updated_at | timestamptz | NO | now() | |

## 索引与约束

任务内容修改通过 `task_adjustment_proposals` 留痕。AI 只能创建 pending 提案，用户确认后才更新 Task；手动修改也创建 applied 审计记录。completed/abandoned/expired Task 不允许覆盖。

- `(user_id, plan_date desc, created_at desc)`；
- partial unique：同一用户最多一个 `status IN ('generated','active')` 的计划；
- `parent_plan_id` 不得等于自身；
- `horizon_start <= plan_date <= horizon_end`；
- weekly_focus_json 必须通过 `WeeklyFocusList` Pydantic Schema，week_index 连续且不重复；
- 新计划成功持久化时，Service 在同一事务归档旧 generated/active/completed 来源计划；事务回滚时旧状态不变；
- evidence_refs_json 中每个引用必须在 Persist 前按用户/Run/类型校验：memory 属于用户且 active，experience_atom 可用，search_source 属于 source_run_id；
- PlanDetail 的 sources 由 Service 按 evidence_refs_json 解析，不把 JSONB 当作任意元数据。
