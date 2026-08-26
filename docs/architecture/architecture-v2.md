# CareerBuddy Agent 架构文档 v2（修复后完整版）

> 版本：v2.0 ｜ 2026-08-26 ｜ 状态：5 项 SLO 全绿，硬门禁 92.2%（SLO ≥85%）
> 本文是答辩主文档：每个设计决策都给出「对比取舍、量化数据、能力边界、演进时机」四要素。

---

## 0. 一句话定位

**CareerBuddy 是一个「确定性工程骨架 + LLM 智能决策」双架构的生产级单 Agent 规划系统**：
代码负责规则、预算、恢复与兜底（保证「可用」），模型负责理解、拆解、生成与非常规决策（保证「好用」），
评测体系负责把两者的贡献分开量化（证明「谁在起作用」）。

这不是「后端 + LLM 调用」。区分二者的判据不是"有没有调大模型"，而是**闭环**：

| 能力 | 普通后端+LLM | 本系统 |
|---|---|---|
| 任务规划 | ❌ 一次 prompt 换一次文本 | ✅ LangGraph 11 节点状态机，模型在图中做意图理解/任务拆解/工具决策 |
| 自我校验 | ❌ 无 | ✅ rule_validator 21 项确定性校验，模型输出必须过闸 |
| 迭代修复 | ❌ 失败重试一次 | ✅ 确定性修复 + 模型自修复 + 降级模板三层闭环 |
| 状态记忆 | ❌ 无状态 | ✅ Run/Personal/Shared 三层记忆，跨会话个性化 |
| 可恢复 | ❌ 崩了重跑 | ✅ Checkpoint + lease/heartbeat/attempt fencing |
| 可归因 | ❌ 只能看结果 | ✅ plan_provenance 事件，模型贡献 vs 工程贡献逐 trial 拆分 |

---

## 1. 设计哲学：双架构（Deterministic Guard × LLM Intelligence）

### 1.1 分工原则

所有子任务按两个轴分类：**出错代价**（错了是否产生非法输出）× **模式稳定性**（输入模式是否高频固定）。

| 象限 | 归属 | 例子 |
|---|---|---|
| 高代价 × 稳定模式 | **确定性代码** | 日期对齐、预算约束、周焦点一致性、敏感内容拦截 |
| 高代价 × 长尾模式 | **代码兜底 + 模型尝试** | 未知业务规则违规 → LLM 分类修复，失败降级拦截 |
| 低代价 × 稳定模式 | **代码** | 意图关键词路由（可回退）、上下文压缩 |
| 低代价 × 长尾模式 | **模型** | 任务拆解、措辞生成、非常规用户请求理解 |

### 1.2 为什么这个哲学能答「伪 Agent」质疑

面试死亡问题「你不就是后端套 LLM 吗」的满分答案结构：
1. Agent 的定义是「感知→规划→行动→校验→修复」闭环，不是「调用了 LLM」；
2. 本系统模型承担的 Agent 职责：意图理解（意图路由）、任务规划（7 天计划拆解）、工具决策（memory/rag/web 三工具）、自我修复（business repair）、非常规 case 处理；
3. 代码承担的生产职责：校验闸门、预算硬限、恢复、降级——这些是任何生产 Agent（含 AutoGPT 系）都必须有的工程层；
4. **量化证据**：双维度归因报告（见 §8）把 92.2% 拆成「模型一次通过 X% + 工程兜底 Y%」，两者都透明，不偷换概念。

---

## 2. LangGraph 选型论证（修复短板 1）

### 2.1 拓扑现状：不是"一条链"

```
START → risk_gate ──(high)──→ safe_response → END
            │(safe)
            ↓
       intent_router ──(navigation)──→ navigation → END
            │(clarification)──→ clarification → END
            │(ready: fan-out，同一超步并发)
            ├──────────────┬──────────────┐
            ↓              ↓              │
      memory_loader ∥ evidence_loader    │
            └──────┬───────┘              │
                   ↓ (join)               │
          context_builder → career_planning_agent → rule_validator
                                                        │(passed)
                                ┌──(repair)────────────┤
                                ↓                      ↓
                         revise_or_fallback → companion_response → persist → END
```

- **原生 fan-out/join 并行**：intent_router 的路由函数返回 `["memory_loader",
  "evidence_loader"]` 节点列表——LangGraph 在同一超步并发执行两个加载器，
  context_builder 作为 join 节点汇聚两份 parcel（`tests/test_parallel_context_fanout.py`
  断言拓扑边与执行区间重叠 >30ms，可复现验证真并发）；
- **3 处条件边**：risk_gate 二分支、intent_router 三分支（含 fan-out）、rule_validator 校验闸门二分支；
- **1 条修复回边**：revise_or_fallback → rule_validator，形成有界循环（BudgetGuard 限 1 次 LLM 修复 + 无限确定性修复）；
- **节点级 Checkpoint**：每个节点执行后落 `agent_checkpoints` 表（run_id + attempt + node_name 唯一），重启后带指纹校验恢复，避免重复烧 LLM 调用；
- **图内并行**：同一工具轮次的多工具调用 `asyncio.gather` 并行执行，保留提交顺序。

### 2.2 与裸函数链的对比（选型不可替代性）

| 维度 | 裸写函数链 | LangGraph（本系统用法） |
|---|---|---|
| 动态分支 | 手写 if/else 散落在函数里 | 声明式条件边，拓扑即文档，可导出可视化 |
| 状态管理 | 手工 dict 传递，无 schema | TypedDict 强类型 State，节点只返回增量 |
| 中断恢复 | 无 | Checkpointer 语义 + attempt fencing，节点粒度重放 |
| 循环控制 | 手写 counter 防死循环 | 条件边 + BudgetGuard 双重有界 |
| 可扩展 | 加节点=改函数签名，连锁修改 | 加节点=注册新 node + 一条边（如后续加 review_agent） |

**并行的工程边界（诚实取舍）**：两个加载器通过 `NodeRunner.run_exclusive`
在共享锁内完成"步骤记账 + DB 读取"的单锁段——锁的启用由**运行时判据**
决定（`_factory_binds_single_connection`：session factory 绑定 Connection
则加锁、绑定 Engine 池则完全免锁），不是人工策略。记忆分支的 **embedding
网络调用在锁外执行**，与证据分支的 DB 读取真实重叠——embed_latency_ms
与各分支 step latency 均已埋点，fan-out 收益 = min(两分支时长) 可从
任意实验的 node step 数据直接计算（诚实预期：毫秒到百毫秒级，收益论证
以架构预留为主、延迟为辅）。另设 `CONTEXT_FANOUT_ENABLED=false` 串行
A/B 模式（记忆分支内联进证据分支单锁段），供对照测量与降级运行。

**什么时候不该用 LangGraph**：纯线性 ETL、单次 LLM 调用的封装层——图运行时是多余依赖。本系统有分支、有环、有恢复需求，三条全占，选 LangGraph 的边际成本小于自研等价物（自研估算：状态通道 + 条件路由 + checkpoint 至少 800–1200 行，且没有社区验证）。

### 2.3 诚实的边界

当前 checkpointer 是自研 Postgres 实现（`CheckpointStore`）而非 LangGraph 内置 SqliteSaver/PostgresSaver——因为恢复语义需要和业务表的 lease/attempt 字段联动，内置 Saver 不知道 Run 的 deadline 和取消标志。这是「用框架的图语义、换掉框架的持久化」的明确取舍。

---

## 3. 工具调用分层架构（修复短板 3）

### 3.1 数据驱动的分层决策

Live 评测发现 GLM-4.7 自主工具触发率仅 11%（9 个需要记忆的 case 里模型几乎不主动调用）。逐案分析后分层：

| 层 | 触发条件 | 执行者 | 依据 |
|---|---|---|---|
| **L1 确定性预执行** | 意图路由器检测到 `references_past_context`（"之前/复盘/经验/偏好"等标记）且意图 ∈ {create_plan, replan} | 代码直接执行 memory_lookup | 高频（用户 78% 的规划请求引用历史）、模式固定、漏调代价高 |
| **L2 模型自主调用** | 其余所有场景，模型在 agent 轮次中自主决定调哪个工具、传什么参数、调几次 | 模型 tool-calling | 低频、非标、组合任务——预执行枚举不完 |
| **L3 预算约束** | 最多 2 轮工具、4 次调用，超限走降级 | BudgetGuard | 防工具循环烧钱 |

### 3.2 「为什么不全部预执行」的答辩逻辑

全预执行 = 退化成规则引擎，遇到「帮我结合行业报告和我的复盘做个新计划」这类组合任务就僵死。保留模型工具选择权是保留 Agent 的泛化能力上限；预执行只是把**已被评测证明模型不可靠的高频路径**钉死成地板。地板保下限（92.2% 硬门禁），模型保上限（未知场景仍有工具可用）。

---

## 4. 三层记忆：分层的量化依据（修复短板 4）

| 层 | 存储 | 生命周期 | 召回策略 | 现在的作用 | 规模化阈值与价值 |
|---|---|---|---|---|---|
| L1 Run | 进程内 state + checkpoints | 单次 Run | 直接引用 | 会话内状态闭环、断点恢复 | 单用户也必需（非过度设计） |
| L2 Personal | `memories` 表（per-user） | 跨会话永久 | 语义向量 + 半衰期（14 天）+ 可信度过滤，上限 5 条/1200 字 | 已上线：replan 连续性、个性化约束 | **用户 ≥1,000**：千人千面。当前 30 case 评测已证明 replan-continivity 依赖此层 |
| L3 Shared | 待建（经验原子表已有雏形） | 全局 | 频次+成功率聚合 | 当前单租户无收益，**刻意未启用** | **用户 ≥5,000**：高频规划模式沉淀为全局模板，跳过部分推理，预估省 15–25% 输出 token |

**实测消融数据**（2026-08-26，`7646a9e8` off / `85d6ba48` on，30 case × k=1，
GLM-4.7，同一份代码仅切 `MEMORY_DISABLED`）：

| 指标 | memory ON | memory OFF | 差值 |
|---|---|---|---|
| 硬门禁通过 | **28/30（93.3%）** | 24/30（80.0%） | **+13.3pp** |
| live-mem 记忆接地 case | 3/3 | 0/3（败因全部为 tool.expected_match） | +3 case |
| 输入 token 均值 | 1988 | 2188 | ON 反而省 9.1%（接地避免修复轮） |
| 记忆召回延迟（memory_loader 节点） | 均值 5ms / 峰值 13ms | —（无此节点） | 并行分支，不在关键路径 |

结论：L2 Personal 层的量化价值不再是估算——**关掉它直接丢 13.3 个百分点**，
且记忆注入的 token 成本被修复环的节省完全覆盖。L3 仍未建（阈值见 §10）。

答辩要点：L3 不是"没做完"，是**明确的不做决策**——单租户阶段写共享层会引入跨用户污染风险（记忆串台是最难排查的生产事故），收益为零。阈值写进了迭代计划（§10），这是按需演进，不是过度设计的补丁说辞。L1/L2 在当前规模已经各有不可替代的作用，有消融实验背书。

---

## 5. 校验-修复闭环：已知确定修、未知不放过（修复短板 5）

### 5.1 三层修复漏斗

```
模型输出 → rule_validator（21 项确定性检查）
              │ 全过 → 直接落地（provenance=model_pass）
              │ 违规
              ↓
        ① 确定性修复（deterministic_repair.py）
              │ 覆盖 4 类高频违规：REPLAN_CONTINUITY / WEEKLY_FOCUS /
              │ FIRST_WEEK_ALIGNMENT / TASK_UNIQUENESS（live 数据：这 4 类占
              │ 全部业务违规的 100%，GLM 自修复成功率 0/6）
              │ 命中且修复后通过 → 落地（provenance=deterministic_repair）
              │ 未命中（未知规则）
              ↓
        ② LLM 修复（1 次预算）——未知违规场景由模型自主归类+尝试修复
              │ 通过 → 落地（provenance=llm_repair）
              │ 失败
              ↓
        ③ 风险降级：fallback 模板（保底可用计划）+ 友好提示 + 快照留档
              → 落地（provenance=fallback，run 标记 degraded）
```

### 5.2 新增的未知规则闭环（本次修复落地）

未知违规码（不在 `DETERMINISTICALLY_REPAIRABLE` 白名单内的）现在会：

1. **运行时**：结构化日志 `agent.repair.unknown_rule` + Prometheus 计数器 `agent_unknown_rule_total{code=...}` + `run.provenance` 事件携带 `unknown_rule_codes` 数组与 LLM 归类标签 `violation_category`（business_repair prompt 要求模型输出违规类型分类，经 ProviderPlanResponse 透传进归因事件）；
   **故障注入测试**（`tests/test_unknown_rule_fault_injection.py`）：注入白名单外的
   `INJECTED_UNKNOWN_RULE` 规则码，端到端断言计数 +1、状态写入、LLM 修复被
   调用、失败后降级模板保底——闭环可复现，不是纸面机制；
2. **离线**：`attribution_report.py` 汇总未知规则 backlog（哪个码、出现几次、LLM 归类分布），**同一码 ≥3 次自动输出"建议提升为确定性规则"**——规则库随 badcase 持续生长的迭代闭环有明确触发阈值；
3. **兜底**：无论 LLM 修复成败，降级路径保证用户永远拿到 schema 合法的可用计划，未知规则永远不可能产生非法输出上线。

这构成「已知规则确定性修复（快、稳、0 token）→ 未知规则模型智能适配（慢、覆盖长尾）→ 长期场景离线沉淀为代码（规则库迭代）」的完整闭环。

**零真实触发的正确解读（runbook）**：backlog 为空 ≠ 机制无效。闭环的验证
标准是「故障注入可复现 + 观测管道与生产同路径」（计数器 / provenance 事件
/ 归因报告三件套都在生产代码路径上，非测试专用）；零触发的含义是"当前 4
条确定性规则覆盖了全部观测违规"——规则库健康的证据。规则库演进流程（一页
runbook）：badcase → violation_category 归类 → 同码 ≥3 次进 backlog 建议 →
人工实现确定性修复 → 故障注入测试 → 合入并从 backlog 清除。

**LLM 修复调用的下线判据（修复5 落地）**：`BUSINESS_REPAIR_LLM_ENABLED`
配置旋钮；判据 = 滚动 100 次调用内 rescue rate（llm_repair provenance 且
硬门禁通过）< 5% → 关闭。归因报告自动输出该比率与建议。当前实测 0/6 →
判据已触发，关闭后该调用保留"未知规则归类探针"角色，仅在 backlog 出现新
高频码时脉冲开启。`revise_or_fallback` 节点同时获得独立 30s 超时上限
（修复9：repair-03 61s 破线的根因是修复节点继承整个 Run deadline）。

---

## 6. 上下文工程：三级压缩而非截断（修复短板 8）

`context_builder` 的压缩流水线（`context_compression.py`，全部确定性、0 LLM 调用）：

| 级 | 机制 | 实现 | 修复前 |
|---|---|---|---|
| ① 混合语义相关性召回 | 老任务与当前请求做 **0.5×bigram 词面 + 0.5×embedding 余弦** 混合打分（查询向量复用记忆分支已算的 embedding，任务向量批量预计算、失败自动降级纯词面），高相关老任务（≥0.08，至多 2 条）**从摘要区提升回保留区** | `_task_relevance` + 同义词回归测试（跳槽/换工作 词面≈0、混合≥0.5） | 纯按时间截断 + 纯词面打分对同义改写全部失效 |
| ② 动态 token 预算 | 序列化上下文超过 `max_input_tokens_per_call` 时，保留窗阶梯收缩（任务地板 2、复盘地板 1） | `budget_shrink_steps` | 固定 5 任务/2 复盘窗口，不管实际 token 量 |
| ③ 摘要式折叠 | 被移出保留区的任务**不删除**：完成项聚合为「更早任务已完成」、放弃项聚合为「主要阻碍」、复盘聚合为「重复阻碍/调整模式」 | `_task_summary` / `_review_summary` | （此级 v1 已有） |

效果指标：`agent.context.compression` 日志记录 promoted_task_count / budget_shrink_steps / 前后字符数，压缩比随历史长度自适应；语义召回确保相关性优先于近因性（recency bias 是截断式压缩的最大质量风险）。

**为什么不用 LLM 做摘要压缩**：压缩在每次规划的必经路径上，LLM 摘要每 run 多 1 次调用（+15–25% 成本、+3–8s 延迟）且引入不确定性；确定性聚合在结构化数据（deliverable/state/blockers）上信息损失极小。LLM 摘要留给未来的 L3 Shared 层（低频离线路径，值得花这个钱）。

---

## 7. 单 Agent 边界与多 Agent 演进（修复短板 7）

### 7.1 为什么现在是单 Agent

本项目任务特性：**单链路**（理解→建上下文→规划→校验→修复→呈现）、**低耦合**（无需多角色对抗）、**延迟敏感**（P95 ≤60s SLO）。多 Agent（AutoGen/CrewAI 式）在此场景的成本：
- 通信开销：每多一个 Agent，一轮协商多 1–2 次 LLM 调用，延迟和成本线性涨；
- 一致性风险：多 Agent 各自输出计划片段，合并后过校验闸的难度指数级上升；
- 调试复杂度：单 Agent 的 trace 是线性的，多 Agent 的 trace 是图上加图。

### 7.2 能力上限与切换时机（量化）

| 信号 | 阈值 | 动作 |
|---|---|---|
| 校验修复循环普遍需要 ≥2 轮 | >20% runs | 拆出独立 reviewer Agent（对抗校验） |
| 工具数增长到模型选择质量下降 | tool-calling 准确率 <80% | 按域拆 Tool-Router 子 Agent |
| 任务需要多角色产物（如"教练+HR 双视角复盘"） | 产品需求出现 | CrewAI 式角色编排（本系统 LangGraph 图直接加节点即可承载） |

关键答辩点：**单 Agent 是当前最优解而非能力上限**——图架构已预留扩展位（新增节点 = 注册 node + 边），从单 Agent 演进到"主 Agent + 子 Agent"是加法不是重写。

---

## 8. 评测体系：六域 × 双维度归因（修复短板 6、9）

### 8.1 两类优化、两套指标，不偷换

| 维度 | 指标 | 优化手段 | 数据 |
|---|---|---|---|
| **工程稳定性**（保可用） | 硬门禁通过率、降级率、P95 延迟、单 run 成本、恢复成功率 | 确定性修复、预执行、降级模板、thinking 禁用 | 72.2%→92.2%；P50 26s→22s，P95 45.5s≤60s；成本 ≤¥0.01/run |
| **模型智能能力**(保好用) | 模型一次通过率（model_pass 占比）、模型自修复成功率、工具决策准确率、检索质量 | prompt 迭代、上下文精简、rubric 校准、数据沉淀 | 由归因报告逐 trial 量化（§8.2） |

**DirectLLM 基线对照实验**（`8d3f4781`，2026-08-26，30 case × k=1，GLM-4.7）：
裸模型直出（无工具/无记忆/无证据可见性）硬门禁 **93.3%** vs 完整 Agent 臂
**83.3%**。这组对照的诚实解读是双面的：

1. **裸模型结构化输出能力已经很强**——这是敢做复杂 Agent 架构的前提，
   不是架构的失败。硬门禁度量的是 schema/规则合规，恰好是裸模型的强项；
2. **Agent 臂的增量价值在硬门禁之外**：记忆接地（replan 连续性、证据引用）、
   工具调用、预算硬限、断点恢复、产出可归因——这些是裸模型臂根本不具备
   的能力维度；
3. **对照暴露了真实短板并转化为迭代目标**：repair-03 / replan-05 两个 case
   裸模型直出通过而 Agent 臂因修复环降级模板失败——修复环降级路径的输出
   质量低于模型直出，是明确的下一优化项（SLO v1.4 已记录）。
4. **后续验证**：图拓扑重构 + 工具调用截断修复后的记忆消融 ON 臂
   （`85d6ba48`，同 30 case）硬门禁回到 **28/30 = 93.3%**——与裸模型基线
   持平，同时保有记忆接地/工具/预算/恢复能力。即"修复环拖累"消除后，
   Agent 臂在硬门禁追平基线、在接地维度超出基线。

这组数据的价值恰恰在于它是**反向发现**：如果只报"Agent 比裸模型好"的
数字才值得怀疑。基线对照让"模型能力 vs 工程价值"的边界第一次有了实测
锚点。

### 8.2 双维度归因（本次新增，代码级落地）

图在 persist 节点写 `run.provenance` 事件，五个互斥标签：

`model_pass`（模型一次通过）｜`format_repair`（格式自修复）｜`deterministic_repair`（代码修复）｜`llm_repair`（模型业务自修复）｜`fallback`（降级模板）

`scripts/attribution_report.py` 连接评测库，对任一 live 实验输出：

```
通过 case 的产出路径拆分 + 模型能力占比 + 工程兜底占比 + 未知规则 backlog
```

**实测数据**（2026-08-26 归因实验 `5cb7e7d7`，30 case × k=1，GLM-4.7 真实模型）：

- 全局硬门禁 25/30 = 83.3%（k=1 单发口径；k=3 全过口径此前为 92.2%）；
- 22 个规划路径 trial 可归因（其余 7 个为澄清/安全拒绝等非规划终态，设计上不写 provenance）：通过 18 个；
- **通过路径拆分：model_pass 15 个（83.3%），deterministic_repair 3 个（16.7%）**；
- 失败路径：fallback 2 个（repair-03 / replan-05 降级）、model_pass 2 个（repair-02 / repair-04，已知 mock-scripted 无效 case）；
- 结论：**通过率的大头是模型一次通过，工程兜底贡献 17 个百分点级的救援**——"全是代码绕过模型"的质疑被同源数据直接否定。

这直接回答「92% 里模型贡献多少、代码贡献多少」——两个数字同源（同一批 trial、同一份硬门禁判定），任何面试官追问都能现场重跑。

### 8.3 六域确定性评分 + 校准

- 六域（task/behavioral/tool/model/system/safety）全部确定性 grader，AuthorizedView 权限隔离，无"LLM 评 LLM"循环；
- 质量维用 rubric judge（v9）+ 双人标注校准：D1 kappa=0.679（过 0.6 门），D3/D4 kappa=0.40 暴露的是**评分标准歧义而非 judge 失准**——已定位为下一迭代（细化 rubric 维度定义），这是评测体系的自洽性证据；
- 检索专项：hybrid Recall@5=1.0、MRR=0.933（20 case 硬化集，含 5 负例 + 改写查询）。

---

## 9. 生产就绪清单（已有能力的量化）

| 能力 | 实现 | 量化 |
|---|---|---|
| SSE 断线恢复 | Last-Event-ID + 原子事件序号（`EventRecorder`，DB 级 sequence 递增） | 任意断点重连零丢失 |
| 崩溃恢复 | lease/heartbeat/attempt fencing，checkpoint 节点粒度恢复 + 输入指纹校验 | 恢复不重复烧 LLM 调用 |
| 超时/预算 | BudgetGuard：LLM 调用数、token、deadline、取消令牌四重硬限 | 单 run 成本 ≤¥0.01，P95 45.5s |
| 限流 | 固定窗口（IP+Auth 哈希桶），429+Retry-After | compose 默认 120/min |
| 可观测 | /metrics（Prometheus 文本）、request_id/run_id/trace_id 三级关联、逐步 trace | 新增 agent_repair_path_total / agent_unknown_rule_total |
| 注入防御 | HTML 注释剥离、中英注入短语中和、假 section 标签破坏 | RAG 文档入库前 Sanitizer |
| 优雅取消 | 取消令牌 + 幂等键 + 终态事件保护 | cancelled 状态不重复计费 |

---

## 10. 未上线能力的量化扩容依据（修复短板 10）

**阶段性迭代原则：当前规模下每一项都已满足 SLO，提前建设的成本/风险大于收益。**

| 模块 | 现状 | 触发阈值 | 届时方案 |
|---|---|---|---|
| L3 Shared 记忆 | 刻意未启用 | 用户 ≥5,000 | 经验原子聚合 + 全局模板库 |
| LLM 摘要压缩 | 确定性聚合已够 | 单 run 输入 token 中位数 >8k | 离线摘要管线（低频路径） |
| 多 Agent | 单 Agent 达标 | §7.2 三信号 | 图内加子 Agent 节点 |
| 分布式状态 | 单节点 Postgres | QPS >50 或日均 run >100k | lease 已是 DB 级，换 Redis/分布式锁零语义变化 |
| 限流升级 | 单机固定窗口 | 多实例部署时窗口不同步 | Redis 令牌桶（中间件已抽象，换实现不动调用点） |
| metrics 外置 | 进程内 registry | 多 worker 部署 | 换 prometheus-client，调用点零修改（模块文档已声明此契约） |

每个阈值都对应一个**可观测的先行指标**（不是拍脑袋）：用户数、QPS、token 中位数、修复循环轮次占比——指标到线即启动迭代，这是可答辩的灰度迭代逻辑。

---

## 11. 量化指标总表（截至 2026-08-26）

| 指标 | 值 | SLO | 状态 |
|---|---|---|---|
| 硬门禁通过率（live, k=3, 30 case） | 92.2% | ≥85% | ✅（72.2→82.2→92.2） |
| 延迟 P50 / P95 | 22s / 45.5s | P95≤60s | ✅ |
| 单 run 成本 | ≤¥0.01 | ≤¥0.01 | ✅ |
| 检索 Recall@5（hybrid, v2 硬化集） | 1.00 | ≥0.90 | ✅ |
| Mock CI 回归 | 798/798 通过 | 100% | ✅ |
| 工具触发率（引用历史场景） | ~100%（预执行） | — | 11%→~100% |
| GLM 业务自修复成功率 | 0/6（已由确定性修复接管） | — | 闭环 |
| 双标注 kappa（D1 质量） | 0.679 | ≥0.6 | ✅ |
| **终版权威口径**（k=3，当前 HEAD，LLM 修复按判据下线） | trial 硬门禁 88.9%；P50/P95 = 20.2s/28.6s；详见 SLO 终版表 | 硬门禁≥85% / P95≤60s | ✅ 宽裕 |
| 双维度归因（2026-08-26 实测，k=1） | 通过路径：模型 83.3% / 工程 16.7% | — | ✅ 新增 |
| 记忆消融（k=1，同代码切换） | ON 93.3% vs OFF 80.0%（+13.3pp）；ON 输入 token 反省 9.1% | — | ✅ 实测 |
| memory_grounded（新质量 grader） | 3/9 = 33%——记忆注入≠计划利用，已暴露为下一迭代目标 | — | ✅ 诚实短板入档 |
| DirectLLM 基线（k=1） | 裸模型 93.3% vs Agent 83.3%→重构后 93.3% | — | ✅ 反向发现入档 |
| fan-out 实测收益 | embedding 226ms（峰值 983ms）与证据分支重叠 | — | ✅ 实测 |

---

## 12. 修复对照表（10 短板 → 10 闭环）

| # | 短板 | 修复落点 |
|---|---|---|
| 1 | LangGraph 使用过浅 | §2：3 条件边 + 修复回边 + checkpoint + 并行工具，选型对比表 |
| 2 | 后端套 LLM 质疑 | §0/§1：双架构哲学 + 六项闭环判据表 |
| 3 | 预执行架空模型 | §3：L1/L2/L3 分层，地板保下限、模型保上限 |
| 4 | 三层记忆过度设计 | §4：分层量化阈值表，L3 是明确的不做决策 |
| 5 | 未知规则无兜底 | §5：三层修复漏斗 + unknown_rule backlog + 降级保底 |
| 6 | 优化偷换概念 | §8.1：工程稳定性 vs 模型能力两套指标体系 |
| 7 | 单 Agent 无边界 | §7：成本清单 + 三量化切换信号 |
| 8 | 压缩=截断 | §6：相关性召回→动态预算→摘要折叠三级流水线 |
| 9 | 贡献无法拆分 | §8.2：plan_provenance 五标签 + 归因脚本（代码级落地） |
| 10 | 扩容无依据 | §10：每项未建能力配阈值 + 先行指标 + 届时方案 |
