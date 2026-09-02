# memories — 已激活长期记忆

| 字段 | 类型 | NULL | 默认/约束 | 说明 |
|---|---|---:|---|---|
| id | uuid | NO | PK | |
| user_id | uuid | NO | FK users.id ON DELETE CASCADE | |
| memory_type | varchar(32) | NO | profile_fact/stable_preference/execution_pattern | |
| summary | varchar(500) | NO | | 检索展示文本 |
| content_json | jsonb | NO | | 结构化内容 |
| sensitivity | varchar(16) | NO | normal/sensitive | 敏感项必须经过候选确认 |
| status | varchar(16) | NO | active/closed | |
| embedding | vector(1024) | YES | | 维度由配置和迁移锁定 |
| source_run_id | uuid | YES | FK agent_runs.id | 来源 |
| version | integer | NO | default 1 | 乐观锁 |
| last_used_at | timestamptz | YES | | |
| created_at | timestamptz | NO | now() | |
| updated_at | timestamptz | NO | now() | |

索引：`(user_id, status, memory_type)`；向量索引只对 active 且 embedding 非空数据建立。

## memory_candidates 决策幂等字段

| 字段 | 类型 | NULL | 说明 |
|---|---|---:|---|
| decision_idempotency_key | varchar(64) | YES | 与 user_id 联合唯一 |
| decision_request_hash | varchar(64) | YES | candidate_id + action 的 canonical SHA-256 |
| decision_action | varchar(16) | YES | confirm/reject |

三个字段必须同时为空或同时有效。同 key 同请求返回第一次决策结果；同 key 不同候选、
不同动作，或已决策候选换用新 key，返回 `STATE_IDEMPOTENCY_KEY_REUSED`。
