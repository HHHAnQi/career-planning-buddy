# OpenAI-compatible LLM Provider

## 目标

用稳定 Protocol 隔离业务代码与具体模型厂商。Agent/Service 只依赖项目 DTO，不引用厂商 SDK 类型。

## 接口

```python
class LLMProvider(Protocol):
    async def generate_structured(
        self,
        *,
        messages: list[Message],
        response_model: type[BaseModel],
        timeout_seconds: float,
        model_alias: str,
    ) -> LLMResult: ...

    async def generate_agent_turn(
        self,
        *,
        messages: list[Message],
        tools: list[ModelToolSpec],
        final_response_model: type[BaseModel],
        timeout_seconds: float,
        model_alias: str,
    ) -> AgentTurnResult: ...
```

`LLMResult` 至少包含：验证后的 data、provider、实际 model、usage、latency_ms、request_id、raw_output_hash。

`AgentTurnResult` 只能是两种互斥结果之一：

- `tool_calls: list[ToolCall]`；
- `final: PlanCandidate`。

同时返回 Tool Call 与 final、自由文本或未知结构都视为 `StructuredOutputError`。

## 环境变量

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_ROUTER_MODEL=
```

运行时通过 model alias 选择主模型/router/reviewer，Trace 记录实际 model id。项目不得把 Codex 或未经验证的模型代号写成运行时依赖。

## 可靠性规则

- 每次调用有节点 timeout，且不能超过 Run 剩余 Deadline；
- Provider 不隐藏业务级重试：每次真正发往模型的请求都必须先从 BudgetGuard 取得额度并写 Trace；
- 只有在请求确定尚未送达上游时，Adapter 才可做 1 次连接级重试；不确定是否已执行时不自动重发；
- Schema 解析失败时 Provider 抛 `StructuredOutputError`，由 Runtime 使用显式 `format_repair` Prompt 修复一次；
- 业务规则 repair 同样由 Runtime 单独发起，Provider 内部不改写输出；
- 记录实际模型 ID、Prompt 版本、usage、latency 和 cost；
- 不在异常中暴露密钥、Authorization Header 或完整敏感输入；
- Provider 不可用时显式抛稳定异常，由 Runtime 决定模板 degraded 或 failed；
- Provider 不得静默切换模型，任何 fallback model 必须来自 config snapshot 并记录。

## Tool Calling 兼容

如果厂商原生支持 Tool Calling，Adapter 转换为统一 `ToolCall(name, arguments)`；如果只支持 JSON Schema，可要求模型输出统一 AgentTurn Schema。上层不依赖具体协议差异。

Tool 定义只来自 ToolRegistry 白名单。模型返回未知 Tool 时不得自动忽略或动态注册。

## Mock Provider

Stage 2 必须先用确定性 Mock 输出跑通：

- happy Plan；
- clarification/high risk；
- Schema invalid → format repair；
- rule invalid → business repair/fallback；
- timeout/cancel。

真实模型不得成为工程骨架、状态机和契约测试的前置条件。

## Wire-level 流式输出（LLM_STREAMING_ENABLED）

`LLM_STREAMING_ENABLED=true` 时，openai_compatible 规划调用走真正的 SSE 流式传输（`stream: true` + `stream_options.include_usage`），默认关闭；Mock Provider 永远不流式，评测确定性不受影响。

分层职责：

| 层 | 行为 |
|---|---|
| `OpenAIChatLLMClient.complete_streamed` | 解析 OpenAI 兼容 SSE 分片：content delta 逐段回调、tool_calls 分片按 index 拼装、末块 usage 归一化；错误映射与遥测与非流式完全一致 |
| `OpenAICompatiblePlanningProvider._complete_request` | 流式开关生效处；组装后的 `LLMResponse` 与非流式逐字段一致，因此图校验、修复、评测回放均不感知差异 |
| `app/providers/streaming.py` | ContextVar sink：图绑定、Provider 读取，`PlanningProvider` 协议与四个实现零签名变更 |
| `StreamProgressPublisher`（`app/harness/stream_progress.py`） | 每次调用最多每 0.5s 追加一条持久 `llm.stream.progress` 事件（仅计数，不含原始文本），SSE 轮询器即可向前端推送实时进度；断线/终态时自我禁用而不中断流 |
| 步级指标 | `AgentStep.trace_data.llm_stream`：chunk 数、字符数、首 token 延迟（ms）、进度事件数——first-token latency 是流式的核心收益指标 |

设计约束：

- **响应语义不变**：流式只改变传输层。任何 Grader、快照或回放都不应感知差异。
- **原始分片不落库**：`agent_events` 只保存计数，防止部分 JSON 与大 payload 污染事实源。
- **进度是尽力而为**：事件写入失败（Run 已终态、数据库瞬断）只禁用发布器，绝不打断 LLM 流。
- 前端把 `llm.stream.progress` 映射为 `progressMessage`（“模型生成中 · 已输出 N 字符”），复用既有 TodayPage 进度展示。
