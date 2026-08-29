# RELEASE_TRUTH_AUDIT.md（2026-08-29，基线 commit `85f83ce` + 本轮硬化提交）

> 方法：逐项以真实代码为准（非 README），STATUS 三档 + 证据定位 + 允许/禁止
> 的对外表述。本轮硬化中修正的两处事实漂移已同步（overview 的 checkpoint
> 与混合检索表述、README 的基线/当前结果拆分）。

## A. Agent Runtime

| 能力 | STATUS | EVIDENCE | CLAIM_ALLOWED | CLAIM_FORBIDDEN |
|---|---|---|---|---|
| Loop | IMPLEMENTED | `graph._generate_candidate` 工具循环；`max_tool_rounds≤2`、`max_tool_calls≤4`（RuntimeConfigSnapshot 约束） | "bounded tool loop (≤2 rounds, ≤4 calls)" | "autonomous unlimited agent loop" |
| State | IMPLEMENTED | LangGraph `StateGraph(PlanningState)` TypedDict（schemas/agent_runs.py） | "typed graph state with declared channels" | "dynamic free-form agent state" |
| Planning | PARTIAL | 单发生成 + schema 校验 + 修复环；无反思式迭代规划器 | "schema-constrained generation with deterministic repair loop" | "reflexive / multi-step planner" |
| Tool execution | IMPLEMENTED | `ToolRegistry.execute` + 同轮 `asyncio.gather` 并行 | "parallel tool execution within a round" | — |
| Context construction | IMPLEMENTED | `memory_loader ∥ evidence_loader` fan-out + `context_builder` join（并发区间断言测试） | "LangGraph native fan-out/join context stage" | — |
| Memory | IMPLEMENTED (L1/L2)；L3 NOT_IMPLEMENTED（明确不建决策） | L1 state/checkpoints；L2 `select_memories` + memory_lookup 工具 + `MEMORY_DISABLED` 消融开关 | "two-layer memory (run + personal) with measured grounding value" | "three-layer memory system"（L3 未建） |
| Persistence | IMPLEMENTED | AgentRun/AgentStep/AgentEvent/ToolCall/AgentCheckpoint 表 | "fully persisted run/step/event/tool records" | — |
| Checkpoint | PARTIAL | planning 节点 candidate checkpoint + 输入指纹复用（`CheckpointStore`、E3 实测 0 新增调用） | "key-node durable checkpoint / result reuse for crash recovery" | "graph-level exactly-once durable execution" |
| Retry / fallback | IMPLEMENTED | 确定性修复（4 规则）+ LLM 修复（下线判据当前关闭）+ 降级模板 | "three-layer repair funnel with sunset criterion" | "LLM self-heals all violations"（0/6 实测） |
| Lease / heartbeat | IMPLEMENTED | `executor._heartbeat_loop` + `lease_expires_at` + `attempt_count` fencing | "PostgreSQL lease-based single-worker runtime" | "HA / multi-replica validated" |
| Crash recovery | IMPLEMENTED（单 worker） | `_release_for_retry` + checkpoint 复用 + E3 测量脚本 | "interrupted runs resume without repeating provider calls" | "multi-process failover tested"（未做） |
| Cancellation | IMPLEMENTED | `cancel_requested_at` + CancellationToken + 幂等取消 | — | — |
| Deadline | IMPLEMENTED | BudgetGuard（deadline/token/calls/取消四重）+ 节点独立上限（revise 30s） | "budget guard with per-node caps" | — |

## B. Tool Runtime

| 能力 | STATUS | EVIDENCE | CLAIM_ALLOWED | CLAIM_FORBIDDEN |
|---|---|---|---|---|
| Registry / schema / 校验 | IMPLEMENTED | `ToolRegistry` + `ModelToolSpec.input_json_schema` + `TOOL_ARGUMENT_INVALID` 错误码 | "schema-validated tool registry" | — |
| Allowlist / intent 限制 | IMPLEMENTED | `available_tools_override`（评测臂断言）+ ToolContext 意图字段 | "tool allowlist with eval-arm invariants" | — |
| 预算 / 轮次 / 超时 | IMPLEMENTED | BudgetGuard + `tool_timeout_seconds` | "bounded tools" | — |
| 错误分类 | IMPLEMENTED | 错误码（TIMEOUT/ARGUMENT/内部）+ 非成功 result 不抛异常 | "explicit tool error taxonomy" | — |
| 结果复用 / replay | PARTIAL | 评测链路 fixture replay（记录/重放）；**运行时无工具结果缓存** | "fixture replay in the eval harness" | "production tool result cache" |
| 持久化 / 审计 | IMPLEMENTED | tool_calls 表 + provider_calls 审计（评测链路）；生产链路靠 Run/Step | — | "per-call cost accounting in production"（cost_cny 未接线，README 已声明） |

## C. Context / Memory

| 能力 | STATUS | EVIDENCE | CLAIM_ALLOWED | CLAIM_FORBIDDEN |
|---|---|---|---|---|
| Context selection / 压缩 / token 预算 | IMPLEMENTED | 三级压缩（混合语义召回→动态预算→摘要折叠），同义词回归测试 | "three-stage compression with hybrid semantic relevance" | — |
| Context snapshot | IMPLEMENTED | `RunInputSnapshot` + `write_input_once` | — | — |
| 记忆候选 / 确认 / 召回 | IMPLEMENTED | `unconfirmed_memory_candidates` + `memory_candidate_distiller` + `select_memories`（pinned+语义+半衰期） | — | — |
| L1/L2 与 Run State 边界 | IMPLEMENTED | L1 进程态/checkpoint、L2 per-user 表 + 工具 | "clear run/personal boundary" | — |
| Hybrid retrieval / reranker | IMPLEMENTED | pgvector+pg_trgm RRF、TEI rerank、宽召窄重排、相对门控；Recall@5=1.0（v2 硬化集） | "hybrid retrieval with GPU reranking" | （旧 overview 曾写"无混合检索/reranker"——**已修正**） |

## D. Eval Harness

| 能力 | STATUS | EVIDENCE | CLAIM_ALLOWED | CLAIM_FORBIDDEN |
|---|---|---|---|---|
| Dataset/Experiment/Trial/Run/Step/ToolCall/Event/Grade | IMPLEMENTED | eval.py 模型族 + 六域确定性 grader + AuthorizedView | "eight-level typed eval data model" | — |
| Pairwise / LLM-judge / 人工校准 | PARTIAL | pairwise 管道 + DeepSeek judge + 双人标注（D1 κ=0.679 过门；D3/D4 κ=0.40，rubric v10 判例已写、**人工重标待排期**） | "dual-annotator calibration with measured κ" | "high-agreement gold dataset"（D3/D4 未达标） |
| frozen config / 回归门禁 | IMPLEMENTED | config_snapshot_json + CI（mock eval 全过 + SLO 退出码） | — | — |
| mock/fixture/live 区分 | IMPLEMENTED | execution_mode 三态 + 臂不变量断言 | — | — |
| 统计效力 | IMPLEMENTED | confidence_report（Wilson CI + 两比例检验）；记忆层 +4.3pp p=0.429 不显著**已如实撤回** | "pre-registered metrics with CI discipline" | "memory layer improves pass rate +13pp"（已撤回） |
