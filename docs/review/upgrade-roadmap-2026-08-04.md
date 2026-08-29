# Career Planning Buddy 本轮升级路线

> 文档日期：2026-08-04  
> 代码基线：`feat/merge-dev-fixes-ly` 最新整合版本  
> 本轮代号：**Stage 6A — Memory Feedback Loop & Context Quality**  
> 建议落位：`docs/review/upgrade-roadmap-2026-08-04.md`

---

## 1. 文档目的

本文件基于当前最新代码重新审查。

本轮升级只解决一个核心问题：

> **让用户执行和复盘产生的长期记忆，经过用户确认后真正进入下一次规划上下文，同时降低上下文冗余，并用可重复测试证明升级有效。**

本轮不是继续堆 Agent 概念，也不是扩建分布式基础设施。所有改动必须服务于下面这个闭环：

```text
用户执行任务
→ 提交复盘
→ 系统提出记忆候选
→ 用户确认
→ 记忆向量化并保存
→ 下一次规划按语义检索相关记忆
→ 计划引用该记忆
→ Trace/Eval 能证明它确实被使用
```

---

## 2. 当前代码基线结论

### 2.1 已经完成且本轮必须保留

当前项目已经具备：

- Guest JWT、用户隔离和 Profile 乐观锁；
- Plan、Task、Review、Replan 完整业务闭环；
- 固定 LangGraph 工作流；
- Run、Step、ToolCall、Event、Snapshot、Replay；
- SSE 持久化、断线续传和唯一 terminal event；
- DeepSeek OpenAI-compatible Provider；
- 本地 `BAAI/bge-large-zh-v1.5` Embedding；
- PostgreSQL 16 + pgvector + HNSW；
- `memory_lookup`、`rag_retrieve`、`web_search` Tool 契约；
- Memory、MemoryCandidate 的查询、确认、拒绝和停用接口；
- 完整业务前端和开发者 Trace 页面；
- Mock Eval、Mock Replay、真实 Provider 冒烟验证；
- SSE Bearer Header、DeepSeek `thinking` 兼容和 OpenAPI Snapshot 等合并修复。

本轮不得破坏以上能力。

### 2.2 当前真实缺口

#### G-1：语义记忆检索没有进入规划主链

`EvidenceRepository.memory_lookup()` 已经支持：

- pgvector cosine similarity；
- 无向量时文本 fallback；
- 用户隔离；
- active 状态过滤。

但 `FixedPlanningGraph._build_context()` 当前只调用：

```python
EvidenceRepository.pinned_memories(...)
```

也就是说，规划只读取“手工 pinned 且最近更新”的记忆，并没有根据用户本次问题进行语义召回。

结果是：

- 本地 BGE 和 HNSW 已经可用；
- `memory_lookup` Tool 已经可用；
- 但普通规划上下文仍然无法自动利用相关长期记忆。

#### G-2：MemoryCandidate 没有生产环境写入者

当前代码有：

- `MemoryCandidate` ORM；
- `MemoryRepository.create_candidate()`；
- 候选查询、确认和拒绝 API；
- 前端“需要你确认”页面；
- 确认后 Embedding 和 Memory 创建逻辑。

但 `create_candidate()` 只在测试中被调用，正常用户复盘不会产生候选。

因此 Memory 页面会长期为空。

需要注意：

> 现有 `distill_evidence.spec.md` 描述的是  
> `SearchSource → ExperienceAtomCandidate`，  
> 并不是 `Review → MemoryCandidate`。

本轮不能把这两个概念混在一起，应新增独立的“记忆候选提炼”设计和实现。

#### G-3：规划上下文过长且边界不清晰

当前 `_build_context()` 最多加载：

- 30 条 Task；
- 7 条 Review；
- 20 条 completed facts；
- 10 条 blocker；
- 3 条 pinned memories。

同时 `generation_messages()` 将整个 `PlanningContext` 作为一个大 JSON 发送给模型。

问题包括：

- Task 与 completed facts 存在重复信息；
- 较旧记录占用上下文但价值不高；
- Profile、历史、记忆、硬约束没有清晰区隔；
- `token_estimate` 目前只按 completed facts 粗略估算；
- 上下文升级效果无法在 Trace 中清楚观察。

#### G-4：缺少针对“记忆是否真正影响计划”的回归测试

现有 30 条 Eval 主要证明：

- 路由正确；
- Schema 正确；
- Validator 正确；
- Repair/Fallback 正确；
- Tool 契约正确。

但还不能充分证明：

- 相关记忆被选中；
- 无关记忆没有被选中；
- 未确认候选不会进入上下文；
- 确认后的记忆会影响后续计划；
- 上下文压缩后输入量确实下降。

---

## 3. 本轮目标与非目标

### 3.1 本轮目标

本轮完成以下四项：

1. **Select：接通长期记忆语义检索；**
2. **Write：从用户复盘中生成待确认 MemoryCandidate；**
3. **Compress + Isolate：压缩历史并重构 Prompt 上下文边界；**
4. **Verify：增加闭环测试、Trace 指标和真实模型冒烟验证。**

### 3.2 本轮完成后的用户体验

用户完成一次 Review 后：

1. 系统根据显式填写的阻碍或调整请求生成 0～2 条候选；
2. 用户在 Memories 页面确认或拒绝；
3. 被确认的候选生成 Embedding，成为 active Memory；
4. 用户下一次请求相关规划时，系统按语义相关性召回；
5. 计划可以通过合法 `evidence_refs` 引用该 Memory；
6. 用户能在 Plan 来源和开发者 Trace 中看到记忆被使用。

---

# 4. 本轮要做的内容

## R-1：语义 Memory Selection 接入主 Graph

### 4.1 目标

用“Pinned 优先 + Semantic Retrieval + Recency + Budget”的方式替换当前纯
`pinned_memories()` 读取方式。

### 4.2 新增模块

建议新增：

```text
backend/app/agent/context_selection.py
```

包含纯函数和小型服务函数：

```python
def build_memory_query(
    *,
    user_message: str,
    goal_type: str,
    blockers: list[str],
    adjustment_request: str | None,
) -> str:
    ...

def recency_score(
    *,
    last_used_at: datetime | None,
    updated_at: datetime,
    now: datetime,
    half_life_days: int = 14,
) -> float:
    ...

def combine_memory_score(
    *,
    similarity: float,
    recency: float,
) -> float:
    # 默认：0.8 * similarity + 0.2 * recency
    ...

def select_memories_within_budget(
    candidates: list[ScoredMemory],
    *,
    max_items: int,
    max_chars: int,
) -> list[ScoredMemory]:
    ...
```

### 4.3 检索规则

固定规则：

1. active 且 pinned 的 Memory 始终优先；
2. 使用本轮用户消息、岗位方向、blockers、adjustment request 组成 query；
3. 调用当前 `EmbeddingProvider.embed()`；
4. 调用 `EvidenceRepository.memory_lookup()`；
5. 排除已选 pinned Memory；
6. 计算：

```text
final_score = 0.8 × semantic_similarity + 0.2 × recency_score
```

7. 低于阈值的记忆不注入；
8. 去重后最多注入 5 条；
9. 总字符预算默认不超过 1200；
10. Embedding 失败时降级为文本检索；
11. 检索失败不得导致整个 Run 失败；
12. 未确认的 MemoryCandidate 永远不能进入检索。

### 4.4 建议配置

在 `Settings` 和 `.env.example` 中增加：

```dotenv
MEMORY_RETRIEVAL_LIMIT=8
MEMORY_CONTEXT_MAX_ITEMS=5
MEMORY_CONTEXT_MAX_CHARS=1200
MEMORY_MIN_SIMILARITY=0.35
MEMORY_RECENCY_HALF_LIFE_DAYS=14
```

测试和 CI 使用 Mock Embedding，不依赖本地模型或网络。

### 4.5 持久化与 Trace

选中的 Memory：

- 写入现有 `RunInputSnapshot.memory_versions`；
- 保留 Memory ID 和版本；
- 更新 `last_used_at`；
- 在 `context_builder` 的 `trace_data` 中记录：

```json
{
  "memory_query_hash": "...",
  "pinned_memory_count": 1,
  "semantic_memory_count": 3,
  "selected_memory_ids": ["..."],
  "selected_memory_scores": [0.82, 0.71, 0.66],
  "memory_fallback_used": false
}
```

Trace 不保存原始敏感 query 或完整敏感文本。

### 4.6 验收测试

至少覆盖：

- pinned Memory 始终优先；
- 相关 Memory 排在无关 Memory 前；
- inactive Memory 不返回；
- 未确认 candidate 不返回；
- Embedding 失败时文本 fallback；
- 低分 Memory 被过滤；
- 用户 A 无法读取用户 B 的 Memory；
- `last_used_at` 被更新；
- Snapshot 中 Memory version 正确；
- 检索失败时基础规划仍能继续。

---

## R-2：Review → MemoryCandidate 最小闭环

### 4.7 目标

让当前已有的 MemoryCandidates 页面真正产生数据。

本轮实现的是：

```text
Review / Task execution facts
→ deterministic candidate distillation
→ MemoryCandidate
→ user consent
→ Memory
```

本轮不实现 `SearchSource → ExperienceAtomCandidate`。

### 4.8 新增模块

建议新增：

```text
backend/app/services/memory_candidate_distiller.py
docs/model-design/agent-nodes/memory_candidate_distiller.spec.md
```

建议输入：

```python
class MemoryDistillationInput(StrictModel):
    user_id: UUID
    source_run_id: UUID | None
    review_id: UUID
    adjustment_request: str | None
    blockers: str | None
    free_text: str | None
    completed_count: int
    abandoned_count: int
    recent_blocker: str | None
```

建议输出：

```python
class ProposedMemoryCandidate(StrictModel):
    memory_type: Literal[
        "stable_preference",
        "execution_pattern",
    ]
    summary: str
    content: dict[str, object]
    sensitivity: Literal["sensitive"]
```

### 4.9 第一版只使用确定性规则

不调用 LLM。

规则示例：

#### stable_preference

存在明确 `adjustment_request` 时生成，例如：

```text
用户希望后续计划减少每日任务量
用户希望将准备重点调整到 Agent 项目
```

#### execution_pattern

满足下面任一条件时生成：

- 当前 blocker 与上次 blocker 相同；
- 当天 abandoned_count ≥ 2；
- 连续两次 Review 指向相同执行阻碍。

示例：

```text
用户多次因时间不足放弃任务
用户反复被环境配置问题阻塞
```

### 4.10 安全和隐私规则

- 每次 Review 最多生成 2 条；
- 所有候选初始均为 `sensitive`；
- 候选只进入 pending 状态；
- 用户确认前绝不写 active Memory；
- 不从自由文本中提取医疗、身份、联系方式等高度敏感信息；
- 不能将原始完整 Review 文本直接复制为 summary；
- summary 应控制在 120 字以内；
- candidate 14 天后自动过期；
- Review 创建成功不能因提炼失败而失败；
- 提炼失败只记录结构化日志。

### 4.11 幂等设计

优先复用 Review 已有的幂等机制：

- 同一 `idempotency_key` 不重复创建 Review；
- candidate 在 Review 创建事务内同步生成；
- `content_json` 写入 `source_review_id` 和规则版本；
- 同一 Review、同一 memory_type、同一规范化 summary 不重复写入。

第一版不强制新增数据库列；如现有 Repository 无法保证幂等，再增加最小迁移。

### 4.12 接入点

在 `ReviewService.create()` 中：

```text
创建 Review
→ 创建 companion message
→ deterministic candidate distillation
→ 创建 0～2 个 pending candidates
→ 提交事务
```

不得把该逻辑放进 Router。

### 4.13 验收测试

至少覆盖：

- adjustment request 生成 stable preference；
- 重复 blocker 生成 execution pattern；
- 普通成功 Review 不生成无价值候选；
- 同一幂等请求不重复生成；
- candidate 默认 pending；
- confirm 后创建带 Embedding 的 Memory；
- reject 后不创建 Memory；
- candidate 过期逻辑；
- 用户隔离；
- distiller 异常不影响 Review 成功。

---

## R-3：Context Compression 与 Prompt Isolation

### 4.14 目标

减少重复历史，将 Prompt 从“大块 JSON”改成明确分区，但不要求模型输出思维链。

### 4.15 新增模块

建议新增：

```text
backend/app/agent/context_compression.py
backend/app/prompts/context_renderer.py
```

### 4.16 历史压缩规则

#### Task

当前最多读取 30 条。

本轮改为：

- 保留最近 5 条完整 Task；
- 更早任务压缩成确定性 summary；
- completed deliverable 最多保留 5 条；
- abandoned blocker 最多保留 3 条；
- 删除 `recent_tasks` 与 `completed_facts` 的重复内容。

示例 summary：

```text
更早任务：已完成 FastAPI API 文档、SSE 测试；
主要阻碍：环境配置、每日可用时间不足。
```

#### Review

- 保留最近 2 条完整 Review；
- 其余只保留重复 blocker 和 adjustment pattern；
- 本轮不增加额外 LLM summary 调用。

### 4.17 Schema 调整

建议在 `PlanningContext` 和 `RunInputSnapshot` 中新增：

```python
task_history_summary: str | None = None
review_history_summary: str | None = None
```

保留原始 ID 列表和版本快照，保证 Replay 可追溯。

### 4.18 Prompt 分区

将当前完整 `PlanningContext.model_dump_json()` 改成有明确标签的上下文：

```xml
<user_request>...</user_request>

<user_profile>...</user_profile>

<planning_window>...</planning_window>

<source_plan>...</source_plan>

<recent_execution>...</recent_execution>

<history_summary>...</history_summary>

<retrieved_memories>...</retrieved_memories>

<evidence_catalog>...</evidence_catalog>

<critical_constraints>...</critical_constraints>
```

规则：

- 用户请求和所有历史内容均视为不可信数据；
- `<critical_constraints>` 放在输出要求前；
- 不让模型输出详细思维过程；
- 只要求模型内部检查约束并输出 Schema 合法 JSON；
- 不把 Eval Test Case 动态注入 Prompt；
- Prompt 版本必须升级，例如：

```text
openai_compatible_plan_stage6_context_v1
```

### 4.19 Token 估算

重写当前过于粗略的 `token_estimate`。

第一版可使用统一估算函数：

```python
def estimate_text_tokens(text: str) -> int:
    # 保守估算，不依赖真实 Provider tokenizer
    ...
```

Trace 记录：

- 压缩前字符数；
- 压缩后字符数；
- 估算 Token；
- Task/Review 被压缩数量。

### 4.20 验收测试

至少覆盖：

- 最近 5 条 Task 原样保留；
- older Task 正确聚合；
- completed facts 不重复；
- Review 压缩正确；
- XML 标签完整且顺序稳定；
- 原始用户输入不能覆盖 system instructions；
- Prompt 不输出显式思维链要求；
- Snapshot 可恢复；
- OpenAPI Snapshot 同步；
- Prompt 版本进入 RuntimeConfigSnapshot。

---

## R-4：升级效果验证

### 4.21 不新增在线 LLM Judge

本轮不将 `quality_reviewer` 接入线上主 Graph。

改用：

- 确定性集成测试；
- Mock Eval；
- 少量真实 Provider 冒烟；
- 人工对比表。

### 4.22 新增回归数据集

建议新增：

```text
backend/evals/datasets/stage6-memory-context-v1.jsonl
```

至少 12 条：

- 3 条相关 Memory 应命中；
- 2 条无关 Memory 不应命中；
- 2 条未确认候选不得命中；
- 2 条 repeated blocker 生成 candidate；
- 1 条 Embedding 失败 fallback；
- 1 条上下文历史很多但仍能完成；
- 1 条用户隔离。

现有 `stage5-v1.jsonl` 保持冻结，不把里面的 Test Case 用作 Few-shot。

### 4.23 真实 Provider 验证

手动运行 5 个 DeepSeek + Local BGE 场景，不进入 CI：

1. 无 Memory 的初次计划；
2. 确认“每日任务减少”后再次规划；
3. 确认“重点做 Agent 项目”后再次规划；
4. 存在无关 Memory；
5. Embedding Provider 不可用时降级。

记录：

- Schema 是否一次通过；
- 是否触发 format repair；
- 是否触发 business repair；
- 是否 fallback；
- 输入/输出 Token；
- 耗时；
- 选中 Memory；
- Plan `evidence_refs`；
- 压缩前后上下文大小。

### 4.24 本轮目标指标

不承诺模型质量绝对提升，但必须达到：

| 指标 | 目标 |
|---|---:|
| 后端原有测试 | 全部通过 |
| 前端原有测试 | 全部通过 |
| Stage 5 Eval | 30/30 不回退 |
| Stage 6 新数据集 | 全部通过 |
| 未确认 candidate 被使用 | 0 次 |
| 跨用户 Memory 泄漏 | 0 次 |
| 重复 Review candidate | 0 条 |
| 大历史 Fixture 上下文字符数 | 至少下降 40% |
| 相关 Memory 在检索 Top 3 | 100% 测试通过 |
| terminal event / Snapshot 契约 | 无变化 |

---

# 5. 建议实现顺序

## Commit 1：基线与测试先行

- 保存升级前测试结果；
- 新增 Stage 6 数据集和失败测试；
- 不改生产逻辑；
- 证明测试在旧代码上会失败。

## Commit 2：Memory Select

- 新增 `context_selection.py`；
- 接入 `_build_context()`；
- 增加配置、Trace、last_used_at；
- 完成语义检索相关测试。

## Commit 3：Memory Candidate Write

- 新增 `memory_candidate_distiller.py`；
- 接入 `ReviewService.create()`；
- 补齐候选闭环测试；
- 前端原则上无需新增页面。

## Commit 4：Compress + Isolate

- 新增压缩模块和 Renderer；
- 更新 Schema、Snapshot、Prompt 版本；
- 更新 OpenAPI Snapshot；
- 运行真实 Provider 冒烟。

## Commit 5：文档和最终验收

- 更新 README 和用户手册；
- 更新 Agent Node README；
- 明确旧 `distill_evidence` 仍未实现；
- 更新本轮验收报告；
- 运行完整检查。

---

# 6. 预计修改文件

## 后端重点文件

```text
backend/app/agent/graph.py
backend/app/agent/context_selection.py              # 新增
backend/app/agent/context_compression.py            # 新增
backend/app/prompts/context_renderer.py              # 新增
backend/app/prompts/career_planning.py
backend/app/schemas/agent_runs.py
backend/app/core/config.py
backend/app/repositories/evidence.py
backend/app/repositories/memories.py
backend/app/services/reviews.py
backend/app/services/memory_candidate_distiller.py  # 新增
backend/app/harness/snapshots.py
backend/evals/datasets/stage6-memory-context-v1.jsonl
backend/tests/test_stage6_memory_selection.py
backend/tests/test_stage6_memory_candidates.py
backend/tests/test_stage6_context_rendering.py
backend/tests/snapshots/openapi.json
```

## 文档

```text
README.md
.env.example
docs/model-design/agent-nodes/README.md
docs/model-design/agent-nodes/memory_candidate_distiller.spec.md
docs/review/upgrade-roadmap-2026-08-04.md
docs/review/stage6a-verification-report.md
```

## 前端

本轮原则上只做必要联调，不新增大页面。

可能修改：

```text
frontend/src/api/types.ts
frontend/src/pages/MemoriesPage.tsx
```

只有 API 契约或候选展示确实需要时才改。

---

# 7. 本轮明确不做的内容及理由

| 不做项 | 本轮不做理由 | 何时重新考虑 |
|---|---|---|
| 真实 Web Search Provider | 当前 Mock Search 已满足 Tool 契约；真实搜索涉及供应商、费用、反爬、来源治理，会分散记忆闭环目标 | 有明确招聘信息来源和用户需求后 |
| `SearchSource → ExperienceAtomCandidate` 的 `distill_evidence` | 当前搜索仍为 Mock，基于假来源沉淀正式知识没有意义 | 接入真实搜索并建立来源审核后 |
| Online `quality_reviewer` 强制执行 | 未经过人工标注校准，会增加成本、延迟和不稳定性；不能替代硬 Validator | 有至少 30 条人工质量标注后 |
| 完整 LLM-as-Judge 平台 | 当前最需要先证明 Memory 闭环；Judge 自身存在偏差，需要独立评测设计 | 本轮闭环稳定后作为 Stage 6B |
| 显式 Chain-of-Thought 输出 | 不需要保存或展示模型详细思维过程；会增加 Token 和数据风险 | 不重新考虑，使用约束清单和简短摘要即可 |
| Best-of-N / Self-Consistency | 会把真实模型调用放大 3 倍以上；当前主要问题是上下文而非候选数量 | 单次生成经真实 Case 证明仍不稳定后 |
| Self-Reflection 二次诊断 | 会增加一次或多次 LLM 调用；现有一次 business repair 足够作为基线 | 有明确失败类型数据后 |
| Validator 全面改 Soft Scoring | 当前多条规则属于契约和安全硬约束；整体软化回归风险高 | 先单独识别可软化的质量规则 |
| LLM Intent Router | 产品前端已提供清晰入口，规则路由当前足够；增加一次模型调用收益有限 | 真实用户出现大量误路由后 |
| HyDE | 当前 Memory 语料小，先接通已有 cosine retrieval；HyDE 会增加一次 LLM 调用 | 普通检索召回率有真实数据不足时 |
| BM25 + Vector Hybrid | 当前尚未使用现成向量检索，直接上 Hybrid 属于提前优化 | Memory/Atom 数量达到数百并出现关键词漏召回后 |
| Reranker | 语料规模和 Top-K 很小，新增模型会增加部署复杂度 | 候选数明显增长后 |
| 更换 BGE 模型或向量维度 | 现有 1024 维 BGE 和数据库已经验证通过；改维度需要迁移和重建向量 | 有跨语言检索数据证明现模型不足后 |
| GPU/CUDA 优化 | 当前本地 CPU 推理已可完成验证；GPU 环境会增加协作和 CI 复杂度 | 实际延迟阻塞用户体验后 |
| 多 Worker / Redis / 消息队列 | 当前执行器明确是单 Worker MVP；没有真实并发或高可用需求 | 上线多人使用或需要多实例部署后 |
| Redis Pub/Sub 替换 SSE DB 轮询 | 现有持久化 SSE 已正确且可续传；本轮无并发容量问题 | 多 Worker 和真实负载出现后 |
| OpenTelemetry / Prometheus / Grafana | Trace 数据已经完整；当前没有长期运行环境和运维指标 | 正式部署并持续运行后 |
| DB 备份和灾备系统 | 当前属于开发/演示环境，不应先建设无人使用的生产运维体系 | 持久化真实用户数据前 |
| 全量拆分 `graph.py` | 文件较大，但全量重构会增加回归风险且不直接改善用户闭环 | 本轮只抽出 Context 相关模块，后续单独重构 |
| TodayPage 全量状态管理重构 | 当前核心流程和测试已经通过；与本轮 Memory 闭环无直接关系 | 前端继续增长或状态 Bug 频繁出现后 |
| Landing Page 和 Analytics | 当前已有 Guest Login、Onboarding 和完整业务页面；本轮技术目标不是获客分析 | 准备公开试用时 |
| 多 Agent | 当前业务适合受控 Workflow，多 Agent 只会增加成本和解释难度 | 出现可独立并行且必须自治的业务角色后 |
| MCP | 当前 Tool Registry 已满足业务能力，不需要额外协议层 | 需要接入外部标准化 Tool Server 后 |

---

# 8. 风险与回滚策略

## 8.1 语义检索误召回

风险：

- 相似度阈值过低；
- 过期偏好被使用；
- Memory 数量少时排序不稳定。

处理：

- pinned 与 semantic 分开；
- active 状态强过滤；
- 最低相似度可配置；
- Trace 保存分数；
- 出现问题可切换：

```dotenv
MEMORY_SEMANTIC_RETRIEVAL_ENABLED=false
```

降级回当前 pinned-only 行为。

## 8.2 Prompt 升级导致真模型回退

处理：

- Prompt 版本升级，不覆盖旧版本名；
- Mock 测试先通过；
- 真模型只做手动冒烟；
- 失败时可切回 legacy renderer；
- 不改 Validator 和 Finalizer 契约。

## 8.3 Candidate 噪声过多

处理：

- 每 Review 最多 2 条；
- 只提取显式 adjustment 或重复 blocker；
- 全部要求用户确认；
- 默认 14 天过期；
- 第一版不用 LLM 自由提取。

## 8.4 敏感数据进入日志

处理：

- Trace 只存 hash、ID、分数和长度；
- 不记录完整 Review、Memory query 或 API Key；
- `content_json` 只存必要结构化来源；
- 继续沿用日志脱敏。

---

# 9. 完整验收命令

Codex 完成后必须真实执行：

```powershell
docker compose config
docker compose up -d postgres
docker compose ps

cd backend
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m alembic current
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m mypy app tests scripts
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m scripts.run_eval

cd ..\frontend
npm test
npm run build

cd ..
.\scripts\check.ps1
git diff --check
git diff --stat
git status --short
```

真实 DeepSeek + Local BGE 冒烟只手动运行，不加入普通 CI。

---

# 10. Codex 执行约束

Codex 开始前必须阅读：

1. `AGENTS.md`
2. `AGENTS.zh-CN.md`
3. `CODEX-CODING-GUIDE.md`
4. 本文档
5. `docs/implementation/stage-4-memory-rag.md`
6. Memory、Review、Agent Runtime、Prompt、Snapshot 相关文档

执行要求：

- 只实施本文“本轮要做”；
- 不实现“本轮明确不做”的内容；
- 基于现有代码增量修改，不重写 Stage 0～5；
- Router 不写业务逻辑；
- Agent Node 不直接操作 ORM；
- MemoryCandidate 必须经过用户确认；
- 测试和 CI 默认使用 Mock Provider；
- 不读取、输出或提交 `.env`；
- 不修改 `docs/design-input`；
- 不降低用户隔离、Schema、事务、幂等、预算或 terminal event 约束；
- 不通过删除测试或放宽类型检查绕过失败；
- 未经用户明确要求，不执行 `git push`；
- 完成后报告修改文件、迁移、测试结果、真实冒烟结果和遗留问题。

---

# 11. 本轮完成定义

只有同时满足下面条件，本轮才算完成：

- Review 能稳定产生有价值的 MemoryCandidate；
- 用户确认后生成 active Memory；
- 下一次相关规划能检索该 Memory；
- 未确认候选不会被使用；
- Plan 能合法引用该 Memory；
- Trace 能解释选中了什么以及为什么；
- 大历史输入明显压缩；
- 旧 30 条 Eval 不回退；
- 新 Stage 6 数据集全部通过；
- 真实 DeepSeek + Local BGE 冒烟成功；
- 所有现有前后端测试和构建通过；
- 文档明确仍未实现的能力，没有把规划项写成已交付。

---

## 最终结论

本轮不是把项目继续做“大”，而是把已经存在但未连通的 Memory、Review、
Embedding、pgvector、Plan Evidence 和前端确认页面连成真正的业务闭环。

完成后，项目最有价值的技术叙事将从：

> “我搭了 Memory 和 RAG 基础设施”

升级为：

> “用户执行反馈会被确定性提炼为待确认记忆；确认后通过本地 BGE 和 pgvector
> 进行带时间衰减及上下文预算的语义检索，并进入后续规划。系统保留快照、引用
> 与 Trace，可证明某条记忆如何影响了新计划，同时未确认敏感信息不会被使用。”
