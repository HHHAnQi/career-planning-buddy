# memory_candidates — 待确认记忆

| 字段 | 类型 | NULL | 默认/约束 | 说明 |
|---|---|---:|---|---|
| id | uuid | NO | PK | |
| user_id | uuid | NO | FK users.id | |
| memory_type | varchar(32) | NO | profile_fact/stable_preference/execution_pattern | |
| summary | varchar(500) | NO | | 用户确认时展示 |
| content_json | jsonb | NO | | |
| sensitivity | varchar(16) | NO | sensitive/highly_sensitive | |
| status | varchar(16) | NO | pending/confirmed/rejected/expired | |
| proposed_by_run_id | uuid | YES | FK agent_runs.id | |
| activated_memory_id | uuid | YES | FK memories.id | |
| expires_at | timestamptz | NO | | Review 自动提炼候选默认 14 天 |
| created_at | timestamptz | NO | now() | |
| decided_at | timestamptz | YES | | |

确认事务：candidate pending→confirmed 与 memories insert 同事务；拒绝/过期不得创建 memory。
