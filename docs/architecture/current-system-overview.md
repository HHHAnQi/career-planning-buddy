# Current System Overview

> 当前系统事实文档。代码、Alembic 迁移和测试是最终依据；历史设计与审查文档只描述对应时间点的状态。

## 产品边界

Career Planning Buddy 是面向计算机专业学生的证据化 AI 求职教练，同时提供开发者侧的 Trace/Eval 工作台。当前用户闭环是：

```text
账号与画像
  → 简历版本 + 目标 JD
  → 求职目标确认 + 路线 + 今日任务
  → 执行进度 + 复盘 + 版本化调整
  → 定向模拟面试 + 逐题分析 + 报告
  → 训练动作 / 个人记忆 / 简历新版本
  → Retest 与跨场次比较
```

普通用户页面包括工作台、今日计划、路线、材料、面试、成长、复盘、记忆和个人设置。持久化角色为 `dev` 的用户额外看到 Run、Eval 和架构页面；前端隐藏链接只是体验控制，FastAPI 的服务端角色校验才是安全边界。

系统不自动投递简历、不代替招聘决策、不提供心理状态判断，也不把一次面试表现解释为稳定能力结论。

## 单体分层与数据边界

```text
api → services → repositories → PostgreSQL
          └→ agent runtime → providers / tools
          └→ harness / eval
```

- Router 只处理 HTTP、JWT、SSE 和错误映射；
- Service 拥有用例、事务、状态机与幂等；
- Repository 拥有 SQLAlchemy 查询和持久化；
- Agent Node 只使用结构化 State/Port，不直接读写 ORM；
- PostgreSQL 16 + pgvector 是业务、Run、事件、快照和 Eval 的事实源；
- Alembic 是唯一数据库结构变更入口。

业务数据按用户隔离。简历、JD、面试 Session/Turn、计划、任务、复盘与个人记忆都通过服务端 JWT Claims 解析出的用户身份访问，不信任请求体中的 `user_id`。

## Agent Runtime

系统不是常驻聊天 Agent，而是由多个短生命周期、可持久化的受控 Graph 组成：

### Career Planning Graph

```text
risk_gate → intent_router → clarification/context_builder
→ career_planning_agent → rule_validator
→ revise_or_fallback → companion_response → persist/finalize
```

它负责首次规划、续接、调整、导航和安全分流。模型可使用的 Tool 由白名单、Schema、超时、预算和用户隔离约束。

### Interview Graph

面试开始、单题回答和报告生成使用独立短 Run。Session 冻结简历/JD 版本；每个 Turn 持久化题目、回答、分析、证据引用和有限追问状态。刷新或进程失败后依靠数据库状态恢复，而不是把整场面试保持在内存 Graph 中。

### Resume Optimization Graph

材料诊断从简历主张、JD 要求和可选面试证据中选择上下文，输出支持度、理由和建议改写。建议必须经过用户接受/拒绝；批量确认后创建带父版本引用的新 ResumeVersion，不覆盖历史文本。

所有 Graph 的结构化模型输出先经过 Pydantic 校验。格式错误最多修复一次；业务规则错误最多进入一次专用修复。预算、截止时间、取消和错误统一收敛到 Finalizer。

## Run、租约与事件

每个 Agent Run 持久化：

- 输入、配置和输出快照；
- 节点 Step、Provider/Tool 调用、Token、延迟和错误；
- 用于 SSE 的 `agent_events`；
- graph/prompt/tool/context 等运行身份；
- terminal status、`result_kind` 和 fallback reason。

事件提交后才向 SSE 客户端发送。断线重连从数据库事件继续，heartbeat 不持久化。每个 Run 恰好一个 terminal event，且必须是最后一个持久化事件。

Run 调度使用 PostgreSQL `SKIP LOCKED` claim、lease、heartbeat、attempt count 和 worker id。过期 lease 可以被接管，旧 attempt 通过 fencing 不能继续写入节点或终态。语义是 at-least-once：重试可复用已持久化 Tool 结果，但可能重新调用 LLM，因此不宣称 exactly-once。

当前完整应用仍部署为一个后端 Worker。Agent Run 具备数据库级恢复边界，但 Eval/Pairwise Executor 仍是进程内执行器，不支持多副本可靠调度。

## 上下文、记忆与证据

```text
L1 Run Working Context
  profile / goal / plan / task / review / resume / JD / interview evidence
  → scene-specific selection → deterministic compression
  → Context Manifest + Input Snapshot

L2 Personal Episodic Memory
  review/report → candidate → user confirm/reject → Memory
  → user-isolated pgvector retrieval → later context/evidence

L3 Reviewed Shared Knowledge
  web search → SearchSource → ExperienceAtomCandidate
  → developer approve/reject → ExperienceAtom
  → pgvector → rag_retrieve/evidence_refs
```

L1 只属于当前 Run。L2 是用户私有并需要确认，未确认或 inactive 的条目不会进入 Prompt。L3 是带来源的共享知识，搜索结果不会自动升级为经验原子，L2 也不会被提升到 L3。

材料与面试上下文使用场景化候选选择、Token 预算和 Context Manifest 记录入选/淘汰原因。Prompt 中的用户材料属于不可信内容，不能通过文本指令改变系统规则或 Provider 配置。

## Provider 与 Tool

应用启动时构建统一 Provider Registry，HTTP 服务和 Agent Tool 复用同一组实例：

- Planning/Goal/Interview/Resume LLM：确定性 Mock 或 OpenAI-compatible；
- Search：Mock 或 Baidu AI Search；
- Embedding：Mock 或预下载的本地 BGE；
- ASR：Mock 或 OpenAI-compatible 单题语音识别。

真实配置缺失或调用失败会显式返回错误，不静默降级到 Mock。密钥使用 `SecretStr` 且只存在于服务端配置，禁止进入 `VITE_*`、快照、Trace 或稳定错误响应。

规划 Agent 的只读 Tool 包括 `memory_lookup`、`rag_retrieve` 和受场景控制的 `web_search`。搜索结果规范化、URL 去重并持久化为 SearchSource；已知域名只作为可靠性先验，不等价于事实认证。

Audio 只支持单题短音频。原始媒体不持久化；ASR 失败不能覆盖或丢失已有文本回答；没有可靠时间戳时不伪造语速或停顿指标，系统不做表情、情绪或心理状态推断。

## 计划、面试和人机确认

- 规划窗口位于用户确认的开始/结束日期闭区间内；
- 中期路线与固定 7 天执行周期分开展示，最终周期可短于 7 天；
- 每天一个关键任务，带起步动作、交付物、预算、检查项和验证状态；
- 重规划归档旧版本并创建新版本，不删除已完成事实；
- 面试使用冻结材料版本和有限问题数，报告结论引用具体 Turn；
- 单次薄弱表现先成为候选，不自动写入长期记忆；
- 报告训练动作、任务调整和简历改写都需要用户确认；
- Retest 只有在维度与证据可比时才输出改善比较。

## Evaluation Harness

```text
Dataset / Case → Experiment → Trial → Agent Run
→ Evidence Projection → Deterministic Grade → Report
```

Experiment 冻结 Git、Graph、Feature Stage、Prompt、Model、Tool、Context、Memory、Search 和 Harness 版本。Provider 模式包括 Mock、Fixture record/replay 和 Live。ProviderCall 审计记录逻辑/物理尝试、延迟、Token、错误元数据与哈希，不保存凭据或隐藏推理原文。

Live Eval 只在显式开发者操作中启用，带有限重试、指数退避、`Retry-After`、节流、并发、deadline 和 cancellation。认证、Schema 和业务契约错误不重试。

Pairwise 支持位置平衡和人工校准。在真实人工样本没有达到门禁前，结果保持 `diagnostic_only`，不能据此声明 Agent 优于直接 LLM。普通 CI 只运行免费、确定性的 Mock/Fixture 检查。

## HTTP 与前端契约

- Pydantic strict Schema 生成 OpenAPI，并用仓库内 Snapshot 检测漂移；
- 前端共享 API Client 统一添加 Bearer Token；
- SSE 使用 Header 鉴权，不把 Token 放进 URL；
- Run 技术状态与用户可见状态分离；
- 开发者页面复用普通登录 Token，但后端额外执行 `require_dev`；
- 浏览器 Eval 控制台只开放小规模 Mock/Fixture 操作，付费 Live Eval 不提供普通用户一键入口。

## 健康、部署与验证

- `/health`：兼容浅层检查；
- `/health/live`：只检查进程存活；
- `/health/ready`：检查 PostgreSQL、Alembic head 和脱敏 Provider 配置，不发起计费调用；
- Docker Compose 启动 PostgreSQL、单 Worker FastAPI 和 React/Nginx；
- `.env.example` 默认全部使用 Mock Provider；
- `scripts/check.ps1` / `scripts/check.sh` 运行 lint、type check、迁移、测试、离线评测冒烟和前端构建。

## 安全与隐私边界

- 账号支持访客与邮箱/密码登录，密码只保存安全哈希；
- JWT Claims 决定身份，dev 角色保存在服务端；
- 简历、JD、回答、音频转写和个人记忆都按敏感数据处理；
- 快照和开发者投影以脱敏字段、摘要和哈希为主；
- L2 严格用户隔离，L3 候选需要真实 Run 来源和开发者审核；
- 简历主张核验是证据范围内的辅助判断，不等同于背景调查；
- 风险内容停止普通规划，不写入长期记忆，并返回受控安全响应。

## 当前已知限制

- 单后端 Worker 部署；Eval/Pairwise 尚无多副本可靠调度；
- Agent Run 已实现关键模型节点（planning）的 durable checkpoint / result
  reuse：相同输入 fingerprint 下恢复时跳过昂贵 provider call（E3 实测 0 次
  新增物理调用）；尚未实现完整 graph-level exactly-once execution；
- 现有兼容 Replay 是明确标记的 `legacy_trace_clone`，不是完整 Graph 重执行；
- 没有 Redis、Celery、Kubernetes、微服务、MCP 或多 Agent；
- 混合检索已实现（pgvector 余弦 + pg_trgm 词面、RRF 融合、TEI GPU
  reranker、"宽召回窄重排"、相对可答性门控）；没有任意网页爬虫或在线
  漂移检测平台；
- 本地 BGE 需要宿主机预下载模型，Compose 不自动下载，也未提供 GPU 镜像；
- 单题 Audio 依赖 Provider 时间戳质量，不保存原始媒体，不支持实时语音或 Video；
- Pairwise 在人工校准不足时只用于诊断；
- 真实 Provider E2E 受网络、密钥、额度、模型版本和本地模型路径影响；
- 当前没有公开托管实例或大规模真实用户价值数据。

这些限制不会改变确定性离线检查的有效性，但在完成集中 Secret、监控告警、备份恢复、数据保留策略和真实用户验证前，项目不应宣称大规模生产就绪。
