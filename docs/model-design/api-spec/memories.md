# Memories API

## GET /api/v1/memories

Query：type、status、include_sensitive、cursor、limit。默认只返回 active 且非敏感记忆。

## PATCH /api/v1/memories/{memory_id}

```json
{"status":"closed","version":2}
```

只允许 active ↔ closed。

## DELETE /api/v1/memories/{memory_id}

物理删除或按产品策略软删除，MVP 实现必须在文档和代码中保持一致。成功 204。

## GET /api/v1/memory-candidates

Query：status，默认 pending。

## POST /api/v1/memory-candidates/{candidate_id}/confirm

需要 Idempotency-Key。事务内持久化 key、request hash 和 action，然后
candidate→confirmed + 创建 memory。相同 key/request 返回原 Memory；复用 key 执行其他
候选或动作返回 409 `STATE_IDEMPOTENCY_KEY_REUSED`。

## POST /api/v1/memory-candidates/{candidate_id}/reject

需要 Idempotency-Key。candidate→rejected，并遵守与 confirm 相同的持久幂等契约。

## 安全

- 高风险 Run 不创建候选；
- 敏感记忆默认不展示；
- 用户只能操作自己的 memory/candidate；
- 记忆内容不得原样进入 Trace。
