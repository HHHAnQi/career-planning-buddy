# 数据模型施工索引

本目录是 SQLAlchemy Model 和 Alembic Migration 的字段级事实源。

## 表清单

| 类别 | 表 |
|---|---|
| 身份与画像 | `users`, `user_profiles` |
| 规划闭环 | `plans`, `tasks`, `reviews`, `companion_messages` |
| 记忆与证据 | `memories`, `memory_candidates`, `search_sources`, `experience_atoms` |
| Agent Runtime | `agent_runs`, `agent_steps`, `tool_calls`, `agent_events`（含输入/配置快照与终态结果） |

Eval 固定数据集第一版保存在 `backend/evals/datasets/*.jsonl`，不阻塞业务迁移。

## 全局规则

- 主键：PostgreSQL `uuid` + `gen_random_uuid()`；
- 时间：`timestamptz`，统一 UTC；
- 软删除只在有恢复需求的表使用，不机械套用；
- 业务更新表使用 `version integer` 乐观锁；
- JSONB 必须有对应 Pydantic Schema，不能变成无约束垃圾桶；
- 金额使用 `numeric(12,6)`；
- 示例 ID 必须是合法 UUID。

完整关系见 [ER 图](./er-diagram.mmd)。
