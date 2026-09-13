# ADR 架构决策记录 v3.0

| ADR | 决策 | 状态 |
|---|---|---|
| 001 | 独立 FastAPI 单体 + React SPA | Accepted |
| 002 | 单核心 Agent + 受控节点 | Accepted |
| 003 | 分层架构：API / Service / Repository / Agent / Provider / Harness | Accepted |
| 004 | PostgreSQL 16 + pgvector 作为唯一权威数据源 | Accepted |
| 005 | 运行时模型使用 OpenAI-compatible Provider，不绑定编码助手 | Accepted |
| 006 | Provider 只保留 LLM / Search / Embedding 三类 | Accepted |
| 007 | Agent Run 使用 PostgreSQL lease 执行，事件持久化后 SSE | Accepted with limitation |
| 008 | LangGraph 负责编排，确定性规则不交给 LLM | Accepted |
| 009 | Spec 驱动但以可运行纵切优先，禁止无限补文档 | Accepted |
| 010 | Agent Run 冻结输入/配置快照并使用唯一终态 Finalizer | Accepted |
| 011 | Eval Harness V2 采用真实重执行语义与逐调用证据可见性 | Accepted |

## ADR-001：独立单体架构

### 决策

项目从零独立开发：

```text
React SPA → FastAPI → PostgreSQL/pgvector
                    → OpenAI-compatible LLM
                    → Search / Embedding Provider
```

不以 ClawAgent 为底座，不依赖其代码、数据库、工具协议和记忆实现。

### 原因

- 两人协作和秋招作品需要清晰的个人贡献边界；
- 单体便于调试、部署和演示；
- 当前规模不需要 Java 微服务或复杂基础设施。

### 演进触发

当出现任一条件再拆分：多团队独立发布、单模块吞吐成为瓶颈、需要独立安全边界。

## ADR-002：单核心 Agent + 受控节点

### 决策

只有 `CareerPlanningAgent` 可以在白名单内选择 Tool。风险判断、状态转移、规则校验、持久化和用户权限由确定性节点或 Service 负责。

### 原因

- 求职计划需要开放生成能力；
- 业务写入和安全边界必须稳定可测；
- 多 Agent 会增加调试和归因难度，MVP 没有必要。

## ADR-003：分层与依赖方向

```text
API → Service → Repository
          └→ Agent → Provider
          └→ Harness
```

- API 不直接访问 ORM；
- Agent 节点不直接写数据库；
- Provider 不暴露厂商 SDK 对象；
- Harness 负责事件、Trace、预算、Snapshot、Finalizer 和评测。

## ADR-004：PostgreSQL + pgvector

### 决策

关系数据、Agent 运行记录、SSE 事件、记忆和向量统一放 PostgreSQL。

### MVP 不引入

Redis、MongoDB、Milvus、Qdrant、对象存储。

### 演进触发

- 多 Worker 可靠任务调度；
- 热点缓存带来明确收益；
- 向量数据量或检索延迟超过 PostgreSQL 能力；
- Trace Artifact 体积不适合数据库。

## ADR-005：编码助手与运行时模型分离

Codex 用于阅读规范、修改代码和执行测试，但不应进入运行时依赖图。

运行时统一配置：

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=...
LLM_API_KEY=...
LLM_MODEL=...
LLM_ROUTER_MODEL=...
```

代码不得写死未经验证的模型项目代号，也不得把 Codex 当作运行时模型。Trace 只记录实际配置的 model id。

## ADR-006：三类 Provider

MVP 仅抽象：

```python
LLMProvider.complete(...)
SearchProvider.search(...)
EmbeddingProvider.embed(...)
```

Cache 和 ObjectStorage 不设空壳 Protocol。需要时再通过 ADR 增加。

## ADR-007：PostgreSQL Lease 执行与 SSE

### 决策

- POST 创建 `agent_runs`；
- PostgreSQL pending Run 是队列事实源；
- Worker 使用 `SKIP LOCKED` claim，并以 lease/heartbeat 维持执行权；
- 进程内 `asyncio.Task` 只执行已 claim 的 Graph；
- 每个事件先写 `agent_events`，再推送 SSE；
- `Last-Event-ID` 按 sequence 恢复；
- lease 过期或优雅停机时 requeue；deadline/attempt 耗尽才 failed。

### 限制

Agent Run 支持跨进程接管，但重试从 Graph 起点开始，属于 at-least-once，不保证 LLM
调用 exactly-once。Eval/Pairwise 尚未采用 lease，因此完整应用的多副本部署仍需限制其执行入口。

## ADR-008：LangGraph 编排

LangGraph 用于：固定条件路由、可序列化状态传递和 Tool Calling 循环。节点 Trace、预算、快照和终态由 Runtime/Harness 包装，不依赖节点自觉实现。

不用 LangGraph 处理：数据库事务、权限、幂等、业务状态机和定时任务。

## ADR-009：文档服务于实现

文档必须足够让编码助手施工，但不得成为阻塞代码的无限前置工作。

执行原则：

1. 冻结当前阶段最小契约；
2. 用 Mock 跑通纵切；
3. 发现错误先改权威 spec；
4. 同步代码和测试；
5. 通过验收后进入下一阶段。


## ADR-010：输入/配置快照与唯一终态

Run 创建时冻结 graph/config snapshot，context_builder 后冻结 input snapshot。Replay 默认使用快照而不是读取当前用户数据。所有 completed/degraded/failed/cancelled 都由 AgentRunFinalizer 写入；persist 通过 finalize_plan 调用，每个 Run 只允许一个 terminal event。

## ADR-011：Eval Harness V2 真实性与证据边界

### 决策

- 复制历史 Run/Trace/结果只称为 `legacy_trace_clone`，不得计为 Replay 或 Eval 通过项；
- Replay 必须从冻结快照重新执行 Graph/Provider/fixture，并产出独立结果和 diff；
- 每次候选生成或修复都冻结 `EvidenceVisibility(call_id, catalog_hash, visible_refs,
  truncated_refs)`；
- `evidence_refs` 只能引用产生当前候选的那次 Provider 调用可见证据；Graph 不自动补引用；
- rule validator 与 finalizer 分别在生成后和持久化前执行相同的可见性约束。

### 原因

Run 级证据池只能证明证据曾经存在，不能证明模型在生成某个候选时看见过它。逐调用可见性
使引用合法性可确定性评估，并避免跨用户证据、被压缩证据和失败 Tool 结果被误持久化。
