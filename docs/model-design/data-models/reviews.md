# reviews — 每日复盘

| 字段 | 类型 | NULL | 默认/约束 | 说明 |
|---|---|---:|---|---|
| id | uuid | NO | PK | |
| user_id | uuid | NO | FK users.id | |
| plan_id | uuid | NO | FK plans.id | |
| review_date | date | NO | | 复盘日期 |
| mood | smallint | NO | CHECK 1..5 | 情绪自评 |
| blockers | varchar(500) | YES | | 阻碍 |
| adjustment_request | varchar(300) | YES | | 明确调整指令 |
| free_text | text | YES | max 1000 由 Schema 保证 | 自由复盘 |
| completed_count | integer | NO | default 0 | Service 从 tasks 计算 |
| abandoned_count | integer | NO | default 0 | Service 从 tasks 计算 |
| suggested_replan | boolean | NO | default false | 是否建议重规划 |
| replan_reason | varchar(500) | YES | | 规则或 Agent 理由 |
| next_plan_run_id | uuid | YES | UNIQUE, FK agent_runs.id | 续接/调整下一计划时创建的 Run |
| idempotency_key | varchar(64) | NO | | 写请求幂等 |
| version | integer | NO | default 1, >=1 | 修改时的乐观锁 |
| created_at | timestamptz | NO | now() | |
| updated_at | timestamptz | NO | now() | |

约束：UNIQUE `(user_id, idempotency_key)`、UNIQUE `(user_id, plan_id, review_date)`。未被下一计划消费时允许 PATCH/DELETE；`next_plan_run_id IS NOT NULL` 后锁定为历史输入事实。

客户端不传 completed_task_ids / abandoned_task_ids，数据库任务状态是唯一事实源。每条 Review 最多创建一个 next_plan_run；即使 `suggested_replan=false`，用户也可在完成复盘后续接次日计划。
