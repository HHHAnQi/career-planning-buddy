# tasks — 可执行任务

| 字段 | 类型 | NULL | 默认/约束 | 说明 |
|---|---|---:|---|---|
| id | uuid | NO | PK | |
| plan_id | uuid | NO | FK plans.id ON DELETE CASCADE | |
| user_id | uuid | NO | FK users.id | 冗余便于隔离查询 |
| title | varchar(120) | NO | | 任务标题 |
| task_type | varchar(16) | NO | learning/project/interview/application/resume/other | 任务类型 |
| scheduled_date | date | NO | | 任务日期 |
| order_index | integer | NO | >=0 | 当日顺序 |
| state | varchar(16) | NO | pending/in_progress/completed/abandoned/expired | |
| starter_action | varchar(240) | NO | | 可立即启动动作 |
| deliverable | varchar(240) | NO | | 可验证产物 |
| rationale | varchar(500) | YES | | 推荐原因 |
| estimated_minutes | integer | NO | CHECK 5..480 | |
| actual_minutes | integer | YES | CHECK >=0 | 完成时填写 |
| abandoned_reason | varchar(32) | YES | too_hard/too_easy/no_time/lost_interest/blocked/other | |
| abandoned_reason_text | varchar(200) | YES | other 时必填 | |
| version | integer | NO | default 1 | 乐观锁 |
| started_at | timestamptz | YES | | |
| completed_at | timestamptz | YES | | |
| abandoned_at | timestamptz | YES | | |
| expires_at | timestamptz | YES | | |
| created_at | timestamptz | NO | now() | |
| updated_at | timestamptz | NO | now() | |

## 约束

- UNIQUE `(plan_id, scheduled_date, order_index)`；
- 当前固定周期默认每天 1 个关键任务；通常 7 天，最终周期可为 1~7 天；每日总时间不超预算由 rule_validator 保证；
- `user_id` 必须与 plan.user_id 一致，由 Service 写入并测试。
