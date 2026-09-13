# 错误响应与业务码

格式：

```json
{"error":{"code":"STATE_VERSION_CONFLICT","message":"...","request_id":"...","details":{}}}
```

| code | HTTP/内部 | 说明 |
|---|---:|---|
| AUTH_INVALID_TOKEN | 401 | JWT 无效或过期 |
| AUTH_FORBIDDEN | 403 | 无权限 |
| NOT_FOUND_PROFILE | 404 | 未建档 |
| NOT_FOUND_RUN | 404 | Run 不存在 |
| NOT_FOUND_PLAN | 404 | Plan 不存在 |
| NOT_FOUND_SOURCE_PLAN | 404 | replan 来源不存在或无权访问 |
| NOT_FOUND_TASK | 404 | Task 不存在 |
| STATE_VERSION_CONFLICT | 409 | 乐观锁冲突 |
| STATE_INVALID_TRANSITION | 409 | 非法状态转移 |
| STATE_RUN_ALREADY_ACTIVE | 409 | 用户已有活动 Run |
| STATE_RUN_ALREADY_FINISHED | 409 | 终态操作冲突 |
| STATE_REPLAN_ALREADY_ACCEPTED | 409 | 重规划已接受 |
| VALIDATION_PROFILE_INVALID | 422 | 画像字段错误 |
| VALIDATION_RUN_INVALID | 422 | Run 请求错误 |
| VALIDATION_REPLAN_SOURCE_UNAVAILABLE | 422 | replan 没有可用来源计划 |
| RESUME_FILE_FORMAT_UNSUPPORTED | 422 | 简历文件格式不支持或扩展名与媒体类型不匹配 |
| RESUME_FILE_SIZE_INVALID | 413 | 简历文件为空或超过上传/解压安全限制 |
| RESUME_FILE_PARSE_FAILED | 422 | 简历文件损坏、加密或无法解析 |
| RESUME_FILE_TEXT_EMPTY | 422 | PDF/DOCX 未包含足够的可提取文本 |
| RESUME_FILE_TEXT_TOO_LONG | 422 | 抽取文本超过 50000 字符 |
| STRUCTURED_OUTPUT_INVALID | 内部 | 模型结构化格式错误 |
| PLAN_RULE_VALIDATION_FAILED | 内部 | 计划规则不通过 |
| TOOL_NOT_ALLOWED | 内部 | Tool 未注册、Stage/意图不允许 |
| TOOL_ARGUMENT_INVALID | 内部 | Tool 参数 Schema 错误 |
| TOOL_TIMEOUT | 内部 | Tool 超时 |
| TOOL_PROVIDER_UNAVAILABLE | 内部 | Tool 外部依赖不可用 |
| BUDGET_EXCEEDED | 内部 | LLM/Tool 预算耗尽 |
| RATE_LIMITED | 429 | 用户级限流 |
| PROVIDER_TIMEOUT | 503/内部降级 | 外部模型超时 |
| PROVIDER_UNAVAILABLE | 503/内部降级 | 外部模型不可用 |
| AGENT_DEADLINE_EXCEEDED | 504/内部状态 | Run 总截止时间 |
| RUN_CANCELLED | 内部 | 用户取消 |
| PROCESS_INTERRUPTED | 内部 | 当前 attempt 中断，Run 可重新入队 |
| AGENT_RETRY_EXHAUSTED | 内部 | lease 重试次数耗尽 |
| PERSISTENCE_FAILED | 内部 | 事务回滚，无可用结果 |
| REPLAY_FIXTURE_MISSING | 422 | deterministic Replay 缺 Tool fixture |
| PROMPT_VERSION_NOT_FOUND | 422 | Replay 所需 Prompt 版本缺失 |

公开 message 不返回堆栈、SQL、API Key、JWT、完整 Prompt 或 Provider 原文。内部错误由 Runtime 映射为 completed/degraded/failed/cancelled，不一定直接作为同步 HTTP 状态返回。
