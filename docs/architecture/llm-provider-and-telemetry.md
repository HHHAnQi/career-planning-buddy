# 统一 LLM Provider 与调用观测设计

## 目标

业务用例只表达“需要模型完成什么”，不拼装 GLM、DeepSeek 或 OpenAI 的 HTTP 参数。
Provider Adapter 负责把统一请求转换成供应商协议，再把响应、Tool Call、Usage 和错误转换回
项目契约。新增供应商不得修改目标理解、规划和证据整理的领域逻辑。

## 分层

```text
GoalUnderstanding / Planning / EvidenceDistillation
  → LLMClient（统一请求与响应）
  → ProviderProfile（能力声明）
  → OpenAIChat wire adapter
  → GLM / DeepSeek / OpenAI-compatible API
```

核心契约位于：

- `app/providers/llm_contracts.py`：Message、Request、Response、Usage、Tool、Telemetry；
- `app/providers/llm_profiles.py`：Provider 能力和任务模型选择；
- `app/providers/llm_client.py`：HTTP 协议、错误映射、响应标准化和安全观测；
- `app/providers/registry.py`：应用级共享客户端和业务 Provider 组合。

## Provider 能力

`LLM_PROVIDER` 表示运行模式，`LLM_PROVIDER_NAME` 表示兼容能力。生产环境推荐显式配置
Provider 名称；`auto` 仅用于兼容旧配置，并且只在组合根中按官方 Host 识别一次。业务代码
禁止通过 URL 判断供应商。

当前能力 Profile：`openai`、`zhipu`、`deepseek`、`openai_compatible`。GLM 和 DeepSeek
把统一的 `reasoning=off` 映射为 `thinking={"type":"disabled"}`；未知兼容网关不会被猜测
注入供应商私有参数。

任务模型可以独立覆盖：规划、目标理解、证据整理。未配置覆盖时回退到 `LLM_MODEL`。

## 观测契约

每次统一 LLM 调用自动产生 `llm.call.completed` 或 `llm.call.failed` 结构化事件，包括：

- operation、provider_id、model_id；
- trace_id、run_id、Provider request_id；
- latency、input/output/reasoning tokens；
- 失败时的稳定 error_code。

禁止记录 API Key、Authorization、原始 Prompt、完整响应和 Chain of Thought。HTTP 请求通过
`X-Request-ID` 建立 trace；异步 Agent Run 使用 `agent-run:{run_id}`，并继续由现有
AgentRun、AgentStep、ToolCall、AgentEvent 保存业务级 Trace。

第一阶段以结构化日志和现有持久化 Trace 为事实源。OpenTelemetry OTLP 导出、Prometheus、
Dashboard、SLO 和告警属于第二阶段；接入时应实现新的 Telemetry Sink，不侵入业务 Provider。

## 扩展新 Provider

1. 在 Profile Registry 声明协议和能力；
2. 若协议仍为 OpenAI Chat，只增加能力/参数映射和契约测试；
3. 若协议不同，实现新的 wire adapter，但保持 `LLMClient` 契约；
4. 增加请求映射、Tool、结构化输出、Usage、错误和 Telemetry 测试；
5. 使用第二个真实 Provider Smoke Test 证明业务代码不需要修改。
