# user_profiles — 用户画像

| 字段 | 类型 | NULL | 默认/约束 | 说明 |
|---|---|---:|---|---|
| user_id | uuid | NO | PK, FK users.id ON DELETE CASCADE | 一对一 |
| goal_type | varchar(32) | NO | CHECK ai_backend/agent_app/backend_java/data_engineer/fullstack/other | 目标方向 |
| stage | varchar(16) | NO | CHECK exploring/preparing/applying/interviewing | 求职阶段 |
| time_budget_minutes | integer | NO | CHECK 15..480 | 每日时间预算 |
| skill_level | varchar(16) | NO | CHECK beginner/intermediate/advanced | 自评等级 |
| skill_summary | text | YES | max 2000 由 Schema 保证 | 技能描述 |
| start_date | date | YES | 应用层新建/更新必填 | 用户指定的计划开始日期；数据库允许 NULL 仅兼容历史数据 |
| deadline | date | YES | 应用层新建/更新必填 | 结束日期，也是计划不得越过的最终边界；数据库暂时允许 NULL 仅兼容历史数据 |
| preferences | jsonb | NO | default '{}' | 时间偏好、目标公司等可演进字段 |
| version | integer | NO | default 1, >=1 | 乐观锁 |
| created_at | timestamptz | NO | now() | |
| updated_at | timestamptz | NO | now() | |

`preferences` Pydantic Schema 第一版只允许：`target_companies`, `preferred_time_slot`, `weekly_available_days`。

约束：`start_date <= deadline`，时间段最长 8 周。历史任一边界为 NULL 的画像不再算完成画像；用户必须自行补齐，迁移不会替用户虚构日期。
