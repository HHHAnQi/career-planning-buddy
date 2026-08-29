# search_sources — 搜索证据快照

| 字段 | 类型 | NULL | 说明 |
|---|---|---:|---|
| id | uuid | NO | PK |
| run_id | uuid | NO | FK agent_runs.id ON DELETE CASCADE |
| url | text | NO | 原始 URL |
| title | varchar(300) | YES | 标题 |
| snippet | text | NO | 清洗、截断后的摘要 |
| source_type | varchar(16) | NO | official/job_board/blog/community/other |
| reliability | numeric(4,3) | NO | 0..1 |
| provider | varchar(32) | NO | 实际 Search Provider |
| retrieved_at | timestamptz | NO | |

约束：UNIQUE `(run_id, url)`。计划响应只能引用本 Run 已保存的 source id，禁止模型凭空生成 URL。
