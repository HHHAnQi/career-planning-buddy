# companion_messages — 陪伴话术

| 字段 | 类型 | NULL | 说明 |
|---|---|---:|---|
| id | uuid | NO | PK |
| user_id | uuid | NO | FK users.id |
| run_id | uuid | YES | FK agent_runs.id |
| plan_id | uuid | YES | FK plans.id |
| task_id | uuid | YES | FK tasks.id |
| review_id | uuid | YES | FK reviews.id |
| trigger_tag | varchar(32) | NO | plan_ready/task_completed/task_abandoned/review_saved/replan_suggested/next_day |
| message | varchar(1000) | NO | 用户可见话术 |
| template_version | varchar(64) | YES | 模板或 Prompt 版本 |
| created_at | timestamptz | NO | |

至少一个关联 ID 非空。普通任务状态更新可使用模板，避免每次都调模型。
