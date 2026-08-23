# ETCLOVG 七层审计：本项目与 Agent Harness 分类法的对照

> 参照框架：*Agent Harness Engineering: A Survey*（2026）提出的 ETCLOVG 七层分类法——
> **E**xecution environment / **T**ool interface / **C**ontext management / **L**ifecycle-Orchestration /
> **O**bservability / **V**erification / **G**overnance。
> 前四层（E/T/C/L）是结构核心，后三层（O/V/G）是控制平面；该综述将 O 与 G 独立成层，
> 并把状态管理归入 L 层。本文档用该分类法审计本系统的覆盖度，并列出各层已知缺口与改进计划。

## 覆盖度总览

| 层 | 覆盖度 | 一句话评价 |
|---|---|---|
| E 执行环境 | 中（合理缺省） | 无沙箱需求（无代码执行类工具）；BudgetGuard 充当逻辑沙箱 |
| T 工具接口 | 强 | 白名单注册表 + Pydantic 契约 + 按意图可见性门控 + 全量调用审计 |
| C 上下文管理 | 强 | 三层记忆与论文"短期/会话级/持久"三视野一一对应 |
| L 生命周期/编排 | 强 | 固定拓扑 + 唯一终态 + 租约恢复；缺节点级断点续跑 |
| O 可观测性 | 强 | 持久化 Trace + 全链路关联 + Prometheus 指标 + 成本归因 |
| V 验证 | 最强 | 六域确定性 Grader + CI 硬门禁 + 反事实 + Pairwise 校准管道 |
| G 治理 | 中（唯一短板） | 身份/脱敏/同意机制齐备；缺结构化 injection 防护与策略形式化 |

## 逐层对照

### E — 执行环境

Agent 代码运行的位置与约束边界。

- 实现：Docker Compose 非 root 单 Uvicorn Worker；PostgreSQL 为唯一状态存储；
  **BudgetGuard（`harness/budget.py`）承担"逻辑沙箱"职能**——总 token、单次输入/输出、
  LLM 调用次数、截止时间、取消令牌全部硬性检查，超限即抛错，不依赖模型自觉。
- 执行权由 PG claim/lease/heartbeat + attempt fencing 管理（`agent/executor.py`）。
- 缺口：无代码执行沙箱——因工具集不含代码执行，属合理缺省（N/A）；多实例水平扩展未验证（P2）。

### T — 工具接口

外部能力的描述、发现与调用。

- 实现：`tools/registry.py` 白名单注册 5 个工具；Pydantic 输入/输出契约 → JSON Schema
  暴露给 function calling；**按意图/场景的可见性门控**（如 web_search 仅在
  requires_fresh_information 时可见）；每次调用全量持久化（args_hash、result_hash、
  latency、error_code）；同 Run 同参数结果复用；结果按 max_result_chars 截断；
  fixture 回放模式支持评测复现。
- 缺口：无 MCP 协议出口（MVP 有意排除，ADR 有记录；改进计划 P1——封装 1-2 个工具为 MCP server）。

### C — 上下文管理

模型在短期/会话级/持久三个视野内能看到什么。

- 实现（与论文三视野几乎一一对应）：
  - 短期 = L1 Run 工作上下文：确定性压缩（近 5 任务/2 复盘保留，更旧折叠为摘要）+ 输入快照落库；
  - 会话级 = L2 个人记忆：0.8×相似度 + 0.2×新近性（14 天半衰期）打分选择，用户确认后才入上下文；
  - 持久 = L3 共享知识：搜索来源 → 候选原子 → 开发者审核 → pgvector 检索。
  - 证据可见性（`harness/evidence.py`）从权限角度控制"能看到什么"——模型不能引用看不见的证据。
- **已加固（2026-09）**：文档级 RAG 上线——`rag_document_chunks` 表（pgvector + pg_trgm 双索引）、
  结构感知分块、RRF 混合检索、RerankProvider（Mock/TEI）、可答复性门控（拒答），
  `document_search` 工具接入规划图；`evals/retrieval-v1` 冻结数据集 +
  Recall@K/MRR/nDCG 三模式评测（`python -m scripts.run_retrieval_eval`）。

### L — 生命周期/编排

控制流如何创建、更新、恢复、消费状态。

- 实现：LangGraph 固定 11 节点拓扑（risk_gate → intent_router → context → agent →
  validate → 最多一次修复 → fallback → companion → persist）；"唯一终态"由部分唯一索引
  强制；崩溃恢复 `recover_interrupted` + 租约接管 + attempt fencing 防旧写；
  幂等取消（Idempotency-Key）；版本化重规划；`checkpoints.py` 服务于回放路径。
- 缺口：节点级断点续跑——Run 重试从图起点重跑（改进计划 P1：最贵节点先做检查点持久化）。

### O — 可观测性

trace、成本、失败与可靠性信号。

- 实现：持久化 Trace（Step/ToolCall/Event/Snapshot 四表）；ContextVar 全链路关联
  （trace_id 贯穿 HTTP→Agent→Provider）；LLM 遥测事件（不记录原始 prompt）；
  Prometheus `/metrics`（请求计数/延迟/在途/限流拒绝，路径标签归一化）；
  **成本归因**（`GET /api/v1/dev/usage-report`：按状态/图/日/Provider 聚合成本、P50/P95 延迟）；
  流式进度事件与首 token 延迟步级指标。
- 缺口：指标进程内存储、重启清零（与单机契约自洽；P1 评估 prometheus-client 或快照落库）；
  无 OTel 导出与告警规则（P2）。

### V — 验证

把任务与 trace 变成评估、失败归因、回归反馈。

- 实现：规则校验器 + 最多一次业务修复 + 确定性 fallback；六域确定性 Grader
  （system/safety/tool/behavioral/task/model，AuthorizedView 越权即抛）；冻结数据集
  + CI 硬门禁（回归反馈）；fixture 录制/回放；反事实消融；Pairwise LLM-Judge +
  人工校准管道（`diagnostic_only` 门控——未达标注量不当作真实结论，这与综述
  "多级判定"的立场一致）；证据伪造校验（引用不可见证据会被拒绝）；bad case 自动导出。
- 缺口：质量层连续分数（rubric 打分 + judge 校准）建设中（见 `docs` 评估方案）；
  检索指标待 RAG 纵切补齐。

### G — 治理

权限、身份、策略、加固、审计、人类监督。

- 实现：JWT 身份（业务请求不认客户端 user_id）；dev 角色门控；SSE Header 鉴权；
  trace 递归脱敏（`harness/redaction.py`）；SecretStr 密钥管理、禁止 VITE_* 泄漏、
  配置审计进 CI；真实 Provider 失败不静默降级；**基于同意的记忆提升（用户确认才入长期上下文，
  对应论文的人类监督机制）**；输入安全（自伤风险正则门控）；生产就绪自审文档。
- 缺口（改进计划）：工具权限矩阵的显式文档化；审计留存策略。
  **已加固（2026-08）**：prompt injection 结构化防护上线——工具入口统一净化管道
  （`tools/sanitization.py`：HTML 注释清除、注入话术中立化、伪造段落标签破坏）+
  渲染层 HTML 转义纵深防御（含越狱测试钉住）。

## 与论文三个跨层命题的对照

综述提出三个跨层系统问题，本项目均有对应答案：

1. **成本-质量-速度三难**：BudgetGuard 把三难变成显式预算参数（token 上限/截止时间/调用数），
   用量报表把三者变成可观测数字。
2. **能力-控制权衡**：项目明确选择了控制优先——固定拓扑而非自由 ReAct、最多一次修复、
   确定性兜底；能力上限让位于可验证性，这是产品定位（求职教练需要可信输出）的主动取舍。
3. **Harness 耦合**：Provider Protocol 隔离外部能力、ContextVar 管道零协议侵入（流式接入时
   Provider 协议未改一行）、评测 harness 与运行时共享事实表但不耦合执行——分层边界清晰。

## 结论

本项目在 T/C/L/V 四层达到或超过综述描述的工程实践，O 层已补强，G 层为唯一实质短板
（injection 加固、策略形式化，见改进计划）。E 层沙箱属合理缺省。

## 参考与备注

- ETCLOVG 引自 *Agent Harness Engineering: A Survey*（2026，arXiv）。该分类法是较新的
  学术提案而非业界事实标准；本文档将其用作**审计框架与叙事脚手架**，覆盖度自评基于
  2026-08 的代码现状，随实现推进更新。
