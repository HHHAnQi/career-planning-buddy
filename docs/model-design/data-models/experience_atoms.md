# experience_atoms — 经验原子

| 字段 | 类型 | NULL | 说明 |
|---|---|---:|---|
| id | uuid | NO | PK |
| goal_type | varchar(32) | NO | 与 Profile GoalType 一致 |
| title | varchar(200) | NO | |
| content | text | NO | 可执行经验片段 |
| evidence_json | jsonb | NO | 来源、适用条件、可信度 |
| embedding | vector(1024) | YES | |
| is_active | boolean | NO | default true |
| created_at | timestamptz | NO | |
| updated_at | timestamptz | NO | |

经验原子不与 search_sources 建强 FK：来源可能是手工整理、官方文档或历史搜索。`evidence_json` 保存来源标识。
