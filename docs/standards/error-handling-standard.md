# 错误处理与降级规范

## 1. HTTP 错误

| 类型 | HTTP | 示例 code |
|---|---:|---|
| 身份无效 | 401 | AUTH_INVALID_TOKEN |
| 无权限 | 403 | AUTH_FORBIDDEN |
| 资源不存在 | 404 | NOT_FOUND_TASK |
| 幂等/状态冲突 | 409 | STATE_TASK_TRANSITION_INVALID |
| 参数或业务校验 | 422 | VALIDATION_PROFILE_INVALID |
| 限流 | 429 | RATE_LIMITED |
| 外部依赖暂不可用 | 503 | PROVIDER_UNAVAILABLE |

响应结构遵守 `contract-standard.md`，生产响应不暴露堆栈和密钥。

## 2. Agent Run 终态

Agent Run 通过异步 API 创建，执行期错误通常不改变 `POST /agent-runs` 的 202 响应，而是收敛到数据库状态和 SSE 终态：

- `completed`：正常 Plan 已持久化，result_kind=plan；
- `degraded`：存在可用但受限的结果，必须有 result_kind 和 fallback_reason；结果可以是模板 Plan、Clarification、Navigation 或 Safe Response；
- `failed`：没有可用结果，必须有 error_code；
- `cancelled`：用户取消，必须有 error_code=RUN_CANCELLED。

持久化失败不能标记 degraded，因为用户没有可读取的结果。每个 Run 只能由 AgentRunFinalizer 写一个 terminal event；persist 通过 finalize_plan 进入同一终态事务。

## 3. 错误分类

| code | 典型处理 |
|---|---|
| PROVIDER_TIMEOUT | 有模板则 degraded，否则 failed |
| PROVIDER_RATE_LIMITED | 有模板则 degraded，否则 failed |
| STRUCTURED_OUTPUT_INVALID | 格式修复一次 |
| PLAN_RULE_VALIDATION_FAILED | 专用 repair 一次 |
| TOOL_ARGUMENT_INVALID | 不重试，Agent 可继续或 fallback |
| TOOL_TIMEOUT | 不无限重试，Agent 可继续或 fallback |
| TOOL_PROVIDER_UNAVAILABLE | 不编造证据 |
| BUDGET_EXCEEDED | 停止外部调用，模板或 failed |
| AGENT_DEADLINE_EXCEEDED | failed |
| RUN_CANCELLED | cancelled |
| PROCESS_INTERRUPTED | 当前 attempt 中止；有 lease 预算则 requeue |
| AGENT_RETRY_EXHAUSTED | failed |
| PERSISTENCE_FAILED | 回滚并 failed |

## 4. 重试与修复

- LLM Schema 格式失败：最多格式修复 1 次；
- 计划业务规则失败：最多专用 repair 1 次，关闭 Tool；
- 网络连接错误：仅对幂等 Provider 调用有限重试，使用短退避且受 Deadline 限制；
- Tool 参数错误不重试；
- Tool 超时不无限重试；
- 数据库事务失败整体回滚；
- 业务副作用不得由 Agent 自由重试。

格式修复和业务修复是两类不同动作，均计入全局 LLM 预算。

## 5. 预算错误

超过 LLM 次数、Tool 轮次/数量或 Run deadline 时，停止后续外部调用。能生成合规模板 Plan 时 degraded，否则 failed。Deadline 到期不继续执行 companion/persist，除非模板已在剩余时间内完成并能原子提交。

## 6. 取消

取消接口先持久化 `cancel_requested_at`，再尝试取消本地 Task。远端 lease owner 通过 heartbeat
读取标记并取消 owner Task，NodeRunner 在节点边界做数据库兜底检查；模型和 Tool 调用前后也
检查取消。用户取消通过 Finalizer 写 cancelled；进程优雅停机释放 lease 并 requeue，
不得伪装成用户取消或业务失败。

## 7. 日志与 Trace

每个异常至少记录 request_id/run_id、错误 code、节点、Provider、重试/修复次数和脱敏摘要。不得记录 JWT、API key、完整敏感输入和外部页面全文。
