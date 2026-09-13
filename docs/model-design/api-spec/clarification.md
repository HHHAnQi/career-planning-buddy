# Clarification 契约

澄清不另设 REST 端点。Agent Run 缺少必要信息或无法支持当前意图时，通过 SSE 发：

```json
{
  "run_id": "...",
  "sequence": 5,
  "questions": ["你目前处于求职准备的哪个阶段？"],
  "slot_names": ["stage"],
  "hint_options": {
    "stage": ["exploring", "preparing", "applying", "interviewing"]
  },
  "reason": "profile_incomplete"
}
```

第一版不在同一 Run 内暂停并恢复：

1. `clarification` 节点把同一内容写入 `agent_runs.result_kind=clarification` 与 `result_payload_json`；
2. 写 `clarification.requested`；
3. 当前 Run 以 degraded 结束；
4. 前端展示澄清表单；
5. 用户 PUT/PATCH Profile；
6. 前端使用新的 Idempotency-Key 创建新 Run。

刷新页面时调用 `GET /agent-runs/{id}` 仍能恢复问题，不依赖 SSE 缓存。后续确有需求再引入 `waiting_input` 与 Checkpoint。
