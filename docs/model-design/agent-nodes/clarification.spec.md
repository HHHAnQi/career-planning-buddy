# clarification — 澄清信息

## 定位

程序节点，不调用 LLM。根据 `missing_slots` 或 unsupported 原因选择固定问题、选项和下一步提示。

## Output

```python
class ClarificationRequest(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=3)
    slot_names: list[str] = Field(min_length=1, max_length=3)
    hint_options: dict[str, list[str]]
    reason: Literal["profile_incomplete", "unsupported_intent", "intent_uncertain"]
```

## MVP 行为

1. 生成 `ClarificationRequest`；
2. 返回 `TerminalBranchResult(result_kind=clarification, payload=...)`；
3. Executor 调用 `AgentRunFinalizer.finalize_degraded()`，在单事务中写 result、`clarification.requested` 和 `run.degraded`；
4. fallback_reason 使用稳定 reason；
5. 前端补 Profile 或回到对应资源页后创建新 Run；
6. 不增加 waiting_input/checkpoint 状态。

前端刷新后可从 `GET /agent-runs/{id}` 重新获得澄清问题，不依赖 SSE 内存。

## 约束

- 最多 3 个问题；
- 只询问本次规划所必需的 slot；
- 不询问与求职规划无关的敏感信息；
- unsupported 的查询型请求应提示使用计划/任务页面，不伪装生成结果；
- 不创建 Plan、Task、Memory 或 SearchSource。
