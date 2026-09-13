# Agent Tool 施工规范

Tool 是 `CareerPlanningAgent` 唯一可以自主选择的外部能力。MVP Tool 对业务状态全部只读；任何 Plan、Task、Review、Memory 写入都必须由业务 Service 完成。ToolRegistry 可以写 tool_calls 和 SearchSource 快照，这是可观测/证据记录，不是模型可控制的业务副作用。

## 1. 统一契约

```python
class ModelToolSpec(BaseModel):
    # 可序列化、真正传给 Provider/模型的定义
    name: str
    description: str
    input_json_schema: dict
    contract_version: str

class RegisteredTool:
    # 仅存在于进程内，不进入 Graph State、Snapshot 或 Provider DTO
    spec: ModelToolSpec
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    timeout_seconds: float = 8.0
    max_result_chars: int = 6000
    stage: int

class ToolContext(BaseModel):
    run_id: UUID
    user_id: UUID
    goal_type: GoalType
    remaining_deadline_ms: int

class ToolResult(BaseModel):
    tool_name: str
    data: dict
    evidence: list[EvidenceItem] = Field(default_factory=list)
    truncated: bool = False
    provider: str | None = None
```

Tool Handler 接收验证后的 Input DTO 和 `ToolContext`，返回验证后的 Output DTO。Handler 不接收 ORM Session；需要数据访问时调用只读 Repository/Service。`RegisteredTool` 不能放入 Graph State；State 和 Provider 请求只能携带可序列化的 `ModelToolSpec`。

## 2. 注册与白名单

`ToolRegistry` 启动时显式注册，不允许通过模型输出动态导入函数。

| Tool | Stage | 可用意图 | 是否联网 |
|---|---:|---|---:|
| `memory_lookup` | 4 | create_plan/replan | 否 |
| `rag_retrieve` | 4 | create_plan/replan | 否 |
| `web_search` | 4 | create_plan/replan 且 requires_fresh_information=true | 是 |

Stage 2/3 的 Agent Tool 列表为空。`context_summarize` 是 `context_builder` 的内部确定性 helper，不暴露给模型，也不记作 Tool Call。

## 3. Tool 输入输出

### 3.1 memory_lookup

```python
class MemoryLookupInput(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5)

class MemoryLookupItem(BaseModel):
    memory_id: UUID
    content: str
    memory_type: str
    relevance: float
    updated_at: datetime
```

约束：Repository 必须按 `user_id` 和 active 状态过滤；返回内容总长不超过 4000 字符。

### 3.2 rag_retrieve

```python
class RagRetrieveInput(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    goal_type: GoalType
    limit: int = Field(default=5, ge=1, le=5)

class RagEvidenceItem(BaseModel):
    atom_id: UUID
    title: str
    content: str
    evidence: str
    reliability: float
    score: float
```

约束：`goal_type` 必须等于当前 Run 的有效目标；模型不能借参数切换用户目标。

### 3.3 web_search

```python
class WebSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5)
    freshness_days: int | None = Field(default=None, ge=1, le=365)
```

输出先由 SearchProvider 标准化为：url、title、snippet、source_type、reliability、retrieved_at。通过 URL 规范化去重后，先写入 `search_sources`，再把已分配的 `search_source` EvidenceItem 交给模型。

## 4. 执行流程

```text
model tool_call
  → 检查 feature stage 与 intent 白名单
  → Pydantic 校验参数
  → 计算 args_hash
  → 同 Run 命中相同 hash 则复用结果
  → 写 tool.called + tool_calls row
  → 检查 deadline / timeout
  → 调用 Handler/Provider
  → 校验、清洗、截断结果
  → 按 Output Schema 做字段级压缩/摘要，保存 replay-safe result_json 与 result_hash
  → 写 tool.returned
  → 以不可信 evidence 包装后回填模型
```

多个 Tool Call 按模型返回顺序执行，MVP 不并行，避免事件顺序和预算难以解释。

## 5. 预算、重试和错误

- 每轮最多 2 个 Tool，总计最多 4 个；
- 单 Tool 默认超时 8 秒，且不得超过 Run 剩余时间；
- ToolRegistry 不做无限重试；Provider 对明确瞬时网络错误最多内部重试 1 次；
- 参数错误不重试，返回 `TOOL_ARGUMENT_INVALID`；
- 超时返回 `TOOL_TIMEOUT`；
- 外部服务不可用返回 `TOOL_PROVIDER_UNAVAILABLE`；
- `web_search` 失败时 Agent 可继续使用本地上下文/RAG；
- `memory_lookup` 与 `rag_retrieve` 均失败且没有足够上下文时，最终走模板降级，而不是编造证据。

## 6. 安全与 Prompt Injection

- Tool 结果、网页文本、记忆内容一律作为不可信数据；
- 删除脚本、不可见控制字符和超长重复片段；
- 使用 `<evidence source_id="...">` 边界回填；
- system prompt 明确禁止执行 evidence 中的指令；
- URL 必须来自 Provider 输出，不接收模型自行生成 URL；
- Trace 的 `args_json/result_json` 必须经过字段级脱敏；
- 不存 API Key、Cookie、Authorization Header 和完整网页正文。

## 7. Replay Fixture

`tool_calls.result_json` 保存经过清洗且不超过 32KB 的结构化结果，作为 Replay fixture。Fixture key 为：

```text
tool_name + args_hash + tool_contract_version
```

Replay 缺 fixture 时默认失败并标记 `REPLAY_FIXTURE_MISSING`，不静默访问真实网络；开发者可显式选择 live 模式，但结果不再视为确定性对比。

## 8. 必测场景

- 未注册 Tool、Stage 未开放、意图不允许；
- 参数 extra 字段、长度和 limit 越界；
- user_id 隔离；
- 相同 args_hash 复用；
- timeout、Provider 失败和结果 Schema 错误；
- web_search URL 去重和 source_id 持久化；
- 结果过长时通过 Tool 专属压缩器保持合法 Output Schema，不能对 JSON 字符串直接截断；
- Prompt injection 文本只作为 evidence；
- Replay fixture 命中和缺失。
