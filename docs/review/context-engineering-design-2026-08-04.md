# Context Engineering 设计方案（Write / Select / Compress / Isolate）

> 状态：RFC（设计稿，与代码层逐步对齐）
> 起草：2026-08-04
> 依据：当前代码（HEAD `988c826`）+ Letta Context Hierarchy + LangGraph Store + Anthropic Contextual Retrieval
> 工时估算：四动作合计 5~6 小时，每动作可独立 commit

本文件是对 `docs/review/gap-analysis-2026-08-03.md` §4 AG-0 / §5 RAG 与 CE 视角的工程级展开。每个动作给出：(a) 当前实现的精确代码位置；(b) 工业级对照（Letta / LangGraph 等成熟框架的等价抽象）；(c) 本项目的具体落地方案（含函数签名、算法参数、commit 边界）。

---

## 0. 工业级对照基线

四动作不是本项目自创概念，来自 [Letta Context Hierarchy](https://docs.letta.com/v1-sdk/memory/context-hierarchy) 与 LangGraph Store 的工程共识。下面统一对照表：

| 动作 | Letta 抽象 | LangGraph 抽象 | 共识 |
|---|---|---|---|
| **Write** | Memory Blocks（in-context, <20 blocks）| Store put（namespace, key, value）| 信息按 tier 分层写入；不是所有数据都进主上下文 |
| **Select** | Archival Memory + `archival_memory_search`（vector DB tool call）| Store search（vector + filter）| 默认 vector 召回 + 时序衰减；不用时间倒序的纯 SQL |
| **Compress** | Message Compaction（`sliding_window` mode；LLM summarizer 把老消息压缩为单条 summary）| `trim_messages` + summarize_messages | 长历史 LLM 总结；阈值触发；保留最近原始 |
| **Isolate** | 独立 Memory Blocks（每个 block 是 labeled context region）| State channels | 显式边界；模型能区分"这段是 profile""这段是 memory"|

---

## 1. Write（写入策略）

### 1.1 当前实现

**位置**：`app/agent/graph.py:_build_context`

**现状**：四类信息全量塞入 `PlanningContext`，仅按 max_length 机械截断：

```python
# app/schemas/agent_runs.py:187
class PlanningContext(StrictModel):
    recent_tasks: list[TaskContext] = Field(default_factory=list, max_length=30)
    recent_reviews: list[ReviewContext] = Field(default_factory=list, max_length=7)
    completed_facts: list[str] = Field(default_factory=list, max_length=20)
    blockers: list[str] = Field(default_factory=list, max_length=10)
    pinned_memories: list[MemoryContext] = Field(default_factory=list, max_length=3)
```

**问题**：
- "30 条历史 task" 个个完整对象（含 deliverable / rationale / scheduled_date 等），约 1500+ token
- `completed_facts` = 历史 task 的 deliverable 列表（20 条），与 `recent_tasks` 中的 completed 状态字段信息冗余
- 无准入策略：无论本次 Run 关心"前端面试"还是"后端项目"，写入的内容完全一致

### 1.2 工业级对照

| 设计点 | Letta Memory Blocks | LangGraph Store | 本项目应对齐 |
|---|---|---|---|
| Tier 分层 | 4 tier（Blocks / Files / Archival / External RAG）| namespace + ttl + index | 4 类信息（profile / tasks / memory / review）分层独立处理 |
| 写入触发 | LLM 主动 insert memory block | 显式 Store.put | `_build_context` 显式按 tier 装配 |
| 准入条件 | "<50k chars, <20 blocks" | manual | 引入 ContextWritePolicy schema |

### 1.3 落地方案

**目标**：把"全量塞 + 机械截断"改为"按 type + state + recency 筛选"。

**新增 schema**：

```python
# app/schemas/agent_runs.py
class ContextWritePolicy(StrictModel):
    """决定 PlanningContext 各 tier 的写入限制"""
    profile: Literal["full", "summary"] = "summary"
    tasks_filter: Literal["all", "active_only", "completed_only"] = "all"
    tasks_limit: int = Field(default=10, ge=0, le=30)
    completed_facts_limit: int = Field(default=8, ge=0, le=20)
    blockers_limit: int = Field(default=5, ge=0, le=10)
    memories_limit: int = Field(default=5, ge=0, le=10)
    reviews_limit: int = Field(default=2, ge=0, le=7)
```

**改 `_build_context`**：按 `policy.tasks_filter` 过滤任务，按 `policy.tasks_limit` 截断；其余字段同理。

**对照效果**：30 task → 10 task（约 -1000 token）；20 facts → 8 facts（约 -300 token）。

**commit 边界**：单 commit，包含 schema + `_build_context` 改造 + PlanningContext.max_length 调整 + 单测守护"policy 严格执行"。

**工时**：1 小时。

---

## 2. Select（选取策略）

### 2.1 当前实现

**位置**：`app/agent/graph.py:_build_context` 调用 `EvidenceRepository.pinned_memories`

**现状**：

```python
# app/agent/graph.py:_build_context
memory_rows = await EvidenceRepository(session).pinned_memories(
    state["user_id"], limit=3
)
# ↑ 按 updated_at 倒序取前 3 条，纯时间排序
```

**但 `EvidenceRepository.memory_lookup` 已实现**（`app/repositories/evidence.py:32-50`）：

```python
async def memory_lookup(self, *, user_id, query, vector, limit):
    if vector is not None:
        distance = Memory.embedding.cosine_distance(vector)
        # ... cosine 距离排序
        return [(memory, max(0, min(1, score))) for memory, score in vector_rows]
    # fallback 到 ilike
```

**问题**：
- **基础设施齐了，主链路未调用**——这是 gap-analysis AG-0 的核心定位
- 即使 0 vector（embedding_provider=mock），`memory_lookup` 的 ilike fallback 也有基本的语义匹配，比纯时间倒序强
- 无时序衰减：3 个月前的 memory 与今天的 memory 同等对待

### 2.2 工业级对照

| 设计点 | Letta `archival_memory_search` | LangGraph Store.search | 本项目应对齐 |
|---|---|---|---|
| 召回算法 | cosine similarity | cosine + 可选 filter | `memory_lookup` 已实现 |
| 时间衰减 | 平台层不强制（让模型自己决定）| 不强制 | **本项目应主动加**——求职规划场景用户偏好会变 |
| 多样性 | — | MMR（最大边际相关性）| Stage 6+ 选项 |
| Token 预算感知 | 通过 Tool 来限制返回量 | 不感知 | **本项目应加**——避免 memory 挤爆 context |

MemGPT 论文（Packer et al. 2023）与 Generative Agents（Park et al. 2023）的 scoring function：

```
score(memory) = α * relevance + β * recency + γ * importance
默认 α=0.6, β=0.3, γ=0.1（本项目用 sensitivity 替代 importance）
```

### 2.3 落地方案

**目标**：用 `memory_lookup` 替换 `pinned_memories`，加 recency + token budget。

**新增模块**：

```python
# app/agent/context_selection.py（新文件）
import math
from datetime import datetime, UTC

HALF_LIFE_DAYS = 14  # 求职场景用户偏好变化半衰期

def recency_score(updated_at: datetime, now: datetime) -> float:
    """指数衰减：14 天半衰期"""
    delta = (now - updated_at).total_seconds() / 86400
    return math.exp(-delta / HALF_LIFE_DAYS)

def score_memory(memory, similarity: float, now: datetime) -> float:
    """score = 0.6 * relevance + 0.3 * recency + 0.1 * normalcy"""
    return (
        0.6 * similarity
        + 0.3 * recency_score(memory.updated_at, now)
        + (0.1 if memory.sensitivity == "normal" else -0.2)
    )

def select_within_budget(
    scored: list[tuple[Memory, float]],
    token_budget: int,
    char_per_token: int = 2,  # 中文 token 粗估
) -> list[Memory]:
    """Token budget aware selection（防止 memory 挤爆 context）"""
    used = 0
    out = []
    for mem, score in scored:
        cost = len(mem.summary) // char_per_token
        if used + cost > token_budget:
            continue
        out.append(mem)
        used += cost
    return out
```

**改 `_build_context`**：

```python
# 替换 pinned_memories 调用
query_vec = (await self._embedding.embed([state["request"].message]))[0]
memories_raw = await evidence_repo.memory_lookup(
    user_id=state["user_id"],
    query=state["request"].message,
    vector=query_vec,
    limit=10,
)
now = datetime.now(UTC)
scored = [(mem, score_memory(mem, sim, now)) for mem, sim in memories_raw]
scored.sort(key=lambda x: -x[1])
selected = select_within_budget(scored, token_budget=400)
pinned_memories = [MemoryContext(...) for mem, _ in selected]
```

**Embedding provider 注入**：`FixedPlanningGraph.__init__` 已有 `embedding_provider` 字段——已经是 graph 层属性，直接复用即可。

**对照效果**： Uncomment mock embedding 时也走 ilike 路径（不再 0 召回）；切换 local embedding（bge-m3）后立刻享受 cos similarity 召回。

**commit 边界**：单 commit，含 `context_selection.py` 新文件 + `_build_context` 改造 + recency / scoring 单测。

**工时**：2 小时。

---

## 3. Compress（压缩策略）

### 3.1 当前实现

**位置**：`app/agent/graph.py:_build_context`

**现状**：0 压缩。30 个 `TaskContext` 完整对象拼进 JSON，每个对象含 8+ 字段（title / state / scheduled_date / deliverable / abandoned_reason 等）。

```python
recent_tasks = [
    TaskContext(
        task_id=task.id, state=TaskStatus(task.state), title=task.title,
        deliverable=task.deliverable, scheduled_date=task.scheduled_date,
        abandoned_reason=task.abandoned_reason,
        abandoned_reason_text=task.abandoned_reason_text,
    )
    for task in task_rows  # 30 条
]
```

加上 `completed_facts = [task.deliverable for completed task][:20]`——和 `recent_tasks` 里 completed 状态字段**信息重复**。

### 3.2 工业级对照

| 设计点 | Letta Compaction | LangGraph summarize_messages | 本项目应对齐 |
|---|---|---|---|
| 触发条件 | context window 满 | 显式调用 | tasks > 10 触发结构化压缩；reviews > 2 触发 LLM 压缩 |
| 算法 | LLM summarizer（claude-haiku / gpt-5-mini）| LLM summarize | 结构化分组（不调 LLM）+ 阈值后 LLM summarize |
| 替换策略 | `sliding_window`：老消息替成单条 summary，新消息保留 | 整体替换 | 保留最近 3 条 raw task；older 用聚合一字符串 |
| 配置 | `sliding_window_percentage=0.3` | — | `compress_threshold=10`，`keep_recent=3` |

### 3.3 落地方案

**两层压缩**：

**Layer 1：结构化压缩（确定性，无 LLM）**

```python
# app/agent/context_compression.py（新文件）
def compress_tasks_structured(
    tasks: list[TaskContext], keep_recent: int = 3
) -> tuple[list[TaskContext], str]:
    """保留最近 N 条原始；older 聚合成结构化字符串"""
    if len(tasks) <= keep_recent:
        return tasks, ""

    recent = tasks[:keep_recent]
    older = tasks[keep_recent:]

    completed = [t.deliverable for t in older if t.state == "completed"][:5]
    abandoned = [t.abandoned_reason_text or t.title for t in older if t.state == "abandoned"][:3]

    summary_parts = []
    if completed:
        summary_parts.append(f"已完成({len(completed)}): {', '.join(completed)}")
    if abandoned:
        summary_parts.append(f"放弃: {', '.join(abandoned)}")

    return recent, "; ".join(summary_parts) if summary_parts else "无显著历史"
```

**效果对照**：30 task × 50 token = 1500 token → 3 recent × 50 + 1 summary × 100 = 250 token（**-83%**）

**Layer 2：Review 历史 LLM 总结（阈值触发）**

```python
async def maybe_summarize_reviews(
    reviews: list[ReviewContext],
    threshold: int = 2,
    llm: PlanningProvider | None = None,
) -> str | list[ReviewContext]:
    """少于阈值直接塞；多用 LLM 压成一句"""
    if len(reviews) <= threshold or llm is None:
        return reviews
    return await llm.summarize_history(
        items=[{"blockers": r.blockers, "adjustment": r.adjustment_request} for r in reviews],
        target="回顾最近 N 次复盘的主要反馈，浓缩成 50 字"
    )
```

**Layer-2 是可选优化**（Stage 6 加），因为加了 LLM 调用成本。Layer-1 是必做基线。

**新字段**：`PlanningContext` 加 `task_history_summary: str | None = None`（存储压缩字符串）。

**commit 边界**：单 commit，含 `context_compression.py` + Layer 1 实现（Layer 2 注释 TODO）+ 单测。

**工时**：2 小时（Layer 1）；Layer 2 另需 1.5 小时。

---

## 4. Isolate（隔离策略）

### 4.1 当前实现

**位置**：`app/prompts/career_planning.py:generation_messages`

**现状**：

```python
def generation_messages(*, message, context, replan_mode):
    payload = {
        "operation": "generate_plan",
        "replan_mode": replan_mode.value,
        "user_request": message,
        "planning_context": context.model_dump(mode="json"),  # ← 整块 JSON
    }
    return _messages(payload)
```

**问题**：整个 `PlanningContext` 序列化成单个 JSON blob 给 LLM。**无任何边界标记**。LLM 在 attention 时无法区分"这段是 profile""这段是 memory"。Lost-in-the-Middle（Liu et al. 2023）证明这种 placement 利用率最低。

### 4.2 工业级对照

| 设计点 | Letta Memory Blocks | LangGraph state channels | Anthropic Prompt 风格 | 本项目应对齐 |
|---|---|---|---|---|
| 显式边界 | 每个 block 独立 labeled | 每个 channel 独立 reducer | `<step>` XML 标签 | XML 标签 |
| 关键信息位置 | blocks 始终在 prompt 头 | — | Anthropic 推荐关键约束尾置 | profile 头，constraint 尾，memory 中 |
| 模型可读性 | block name 显式 | — | XML 解析友好（GLM / Claude 都接受）| 同 |

### 4.3 落地方案

**目标**：把扁平 JSON 改为带 XML 标签的分块 prompt。

**新增 renderer**：

```python
# app/prompts/context_renderer.py（新文件）
import json

def render_context_xml(ctx: PlanningContext, user_msg: str) -> str:
    """CE-Isolate：四动作分区输出，对抗 Lost-in-the-Middle"""
    profile = ctx.profile

    # Header：用户画像（关键固化信息放头部）
    header = f"""<user_profile goal_type="{profile.goal_type}" stage="{profile.stage}"
              daily_budget="{ctx.time_budget_minutes}min" skill="{profile.skill_level}">
  {profile.skill_summary or ""}
</user_profile>"""

    # Window：规划窗口
    window = f"""<planning_window start="{ctx.planning_window.horizon_start}"
                 end="{ctx.planning_window.horizon_end}"
                 weeks="{ctx.planning_window.horizon_weeks}" />"""

    # 中段：可变上下文（history + memory）
    history_block = ""
    if ctx.recent_tasks:
        task_items = "\n".join(f"  - [{t.state}] {t.title} → {t.deliverable}"
                               for t in ctx.recent_tasks[:3])
        history_block = f"<recent_history>\n{task_items}\n</recent_history>"

    if ctx.task_history_summary:
        history_block += f"\n<older_history>{ctx.task_history_summary}</older_history>"

    memory_block = ""
    if ctx.pinned_memories:
        mem_items = "\n".join(f"  • {m.summary}" for m in ctx.pinned_memories)
        memory_block = f"<retrieved_memories>\n{mem_items}\n</retrieved_memories>"

    # 尾部：硬约束（确保模型 attention 覆盖规则）
    constraints = f"""<critical_constraints>
  - 任务数 1~3
  - 每任务 estimated_minutes ≤ {ctx.time_budget_minutes}
  - 任务 scheduled_date ∈ [{ctx.planning_window.horizon_start}, {ctx.planning_window.horizon_end}]
  - weekly_focus 数量必须 == {ctx.planning_window.horizon_weeks}
  - evidence_refs 必须在 supplied evidence_catalog 范围内
</critical_constraints>"""

    # 用户消息始终最后（保证 attention 高位）
    request = f"<current_request>{user_msg}</current_request>"

    return "\n\n".join(b for b in [header, window, history_block, memory_block, constraints, request] if b)
```

**改 `generation_messages`**：

```python
def generation_messages(*, message, context, replan_mode):
    user_context = render_context_xml(context, message)
    payload = {
        "operation": "generate_plan",
        "replan_mode": replan_mode.value,
        "structured_context": user_context,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT_V2},
        {"role": "user", "content": user_context},
    ]
```

**SYSTEM_PROMPT_V2 升级**（与 §AG-1 六格 CoT 骨架合并）：

```python
SYSTEM_PROMPT_V2 = """你是职业规划引擎。严格按骨架推理：

<goal>从 <user_profile> + <current_request> 推导本次规划的核心目标</goal>

<rules>从 <critical_constraints> 复述 5 条必须遵守的硬约束</rules>

<assets>评估 <retrieved_memories> 与 <recent_history> 中可复用的事实</assets>

<draft>列出 3 个候选任务（内部思考，不进 JSON）</draft>

<verify>对照 rules 自检每个候选</verify>

<output>输出 PlanCandidate JSON</output>
"""
```

**对照效果**：
- LLM 在 attention 时通过 `<user_profile>` / `<recent_history>` / `<critical_constraints>` 显式分区
- 关键约束放尾部对抗 Lost-in-the-Middle
- `<output>` 前的 `<draft>` + `<verify>` 是隐式 CoT，等于把六格 CoT 内嵌进 Isolate（一举两得）

**commit 边界**：单 commit，含 `context_renderer.py` + SYSTEM_PROMPT_V2 + `generation_messages` 改造 + 单测验证 XML 结构。

**工时**：1 小时。

---

## 5. 四动作实施顺序与依赖

| 顺序 | 动作 | 依赖 | 工时 | 独立 commit？|
|---|---|---|---|---|
| 1 | **Select** | 无（基础设施已就绪）| 2h | ✅ |
| 2 | **Isolate** | 依赖 Select 完成（XML 渲染要用 Select 的输出结构）| 1h | ✅ |
| 3 | **Compress** | 独立（task 压缩不依赖其它）| 2h | ✅ |
| 4 | **Write** | 独立 | 1h | ✅ |

**总工时 6 小时，4 个独立 commit**。每个 commit 都附单测，主链路 ruff/mypy/pytest 全过。

---

## 6. 验收标准

实施完后，可在 `agent_steps.trace_data` 验证四动作落地：

| 检查项 | 验证方法 | 期望 |
|---|---|---|
| Select 接通 | trace 中 `context_builder` 节点 `pinned_memories` 来源从 SQL 时间倒序改为 `memory_lookup` | 无 pinned_memories 时返回 `[]`；有 memories 时按相关性排序 |
| 压缩生效 | trace 中 `context_builder.tokens_in` 数值 | 较改造前下降 30%~40% |
| Isolate 生效 | prompt 日志（dev trace 可看 raw request body）| 含 `<user_profile>` / `<recent_history>` / `<critical_constraints>` XML 标签 |
| Write 限制生效 | PlanningContext 字段长度 | tasks ≤ 10，completed_facts ≤ 8，reviews ≤ 2 |
| 真模型通过率 | 跑 5 次真实 create_plan | degraded 率显著降低（目标 < 20%）|

---

## 7. 工业级完备性自检

对照工业 CE 五大标志（来自 §0 Letta + LangGraph 跨项目共识）：

| 标志 | 本方案是否满足 | 实现位置 |
|---|---|---|
| 多 tier 分层 | ✅ | PlanningContext 已显式字段分层 |
| Relevance-weighted 召回 | ✅ | `context_selection.py:score_memory` |
| Time decay | ✅ | `context_selection.py:recency_score`（14 天半衰期）|
| Token budget aware | ✅ | `context_selection.py:select_within_budget` |
| 压缩有触发条件 | ✅ | `context_compression.py:compress_tasks_structured`（threshold=10）|
| LLM summarize（可选 Layer 2）| ⚠️ Stage 6 | `maybe_summarize_reviews`（标 TODO）|
| Prompt 内显式边界 | ✅ | `context_renderer.py:render_context_xml` |

7 项中 6 项满足，1 项标 Stage 6。**判定为工业级基线完备**。

---

## 8. 与 gap-analysis 的关系

本 RFC 实现了 `docs/review/gap-analysis-2026-08-03.md` 的：
- AG-0（pgvector memory retrieval 接通） = §2 Select
- AG-1（Prompt CoT 骨架）= §4 Isolate 中的 SYSTEM_PROMPT_V2
- §5 RAG 视角的 L1-L4 层（写入/选取/压缩/放置）= §1~§4 完整对照

完成后这两条 gap 状态从 ❌ 闭到 ✅，可在 gap-analysis 增量更新中滚动。

---

## 9. 引用

1. [Letta, "Context Hierarchy"](https://docs.letta.com/v1-sdk/memory/context-hierarchy) — Memory Blocks / Archival Memory 四 tier 模型
2. [Letta, "Compaction"](https://docs.letta.com/v1-sdk/messages/compaction) — sliding_window LLM summarizer
3. [LangGraph Memory Concepts](https://langchain-ai.github.io/langgraph/concepts/memory/) — Store + trim_messages
4. [Packer et al. "MemGPT" (2023)](https://arxiv.org/abs/2310.08560) — α*relevance + β*recency + γ*importance scoring
5. [Park et al. "Generative Agents" (2023)](https://arxiv.org/abs/2304.03442) — recency 衰减函数
6. [Liu et al. "Lost in the Middle" (2023)](https://arxiv.org/abs/2307.03172) — context position 影响利用率
7. [Anthropic, "Building Effective Agents" (2024)](https://www.anthropic.com/research/building-effective-agents) — 工作流分类 + XML prompt 风格
8. [Anthropic, "Contextual Retrieval" (2024)](https://www.anthropic.com/news/contextual-retrieval) — 给 chunk 加 contextual summary 再 embed
