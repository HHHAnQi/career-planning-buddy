> [!WARNING]
> **历史归档，不作为当前实现依据。** 本文保留早期调研与设计轨迹，其中的技术选型、阶段编号、模型名称、状态枚举和安全资源可能已经过时。当前实现必须以根 README 的“文档权威顺序”为准。

# ADR 架构决策记录（Architecture Decision Records）

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-07-10 |
| 关联文档 | [AI规划搭子_PRD_v1.0](./02_产品需求文档PRD_v1.0.md) |
| 文档目的 | 承接从 PRD 抽离的技术实现决策，作为架构设计和开发的技术契约 |

---

## ADR-001：整体架构与服务边界

### 决策

采用 **Java（Spring Boot）+ Python（FastAPI + LangGraph）** 双服务架构。

### 架构图

```
微信小程序
    │ HTTPS
    ▼
Nginx (443, 反代 + HTTPS + 小程序合法域名)
    │
    ├──→ Java Spring Boot (:8080)
    │       ├─ 小程序 API（用户/画像/计划/任务/复盘/记忆 CRUD）
    │       ├─ 权限与事务
    │       ├─ 内容安全审核接入（msgSecCheck）
    │       ├─ 后台管理 API
    │       └─ 调用 Python Agent 服务
    │
    ├──→ Python FastAPI + LangGraph (:8000)
    │       ├─ /agent/plan/run（4 节点工作流）
    │       ├─ LLM 调用（DeepSeek / GLM）
    │       ├─ Web Search 调用
    │       └─ RAG 检索
    │
    ├──→ PostgreSQL (:5432, 含 pgvector)
    │       ├─ 业务表（users/user_profiles/plans/tasks/...）
    │       └─ 向量表（memories/experience_atoms embedding）
    │
    └──→ Redis (:6379)
            ├─ 会话缓存
            ├─ 短期上下文
            └─ 限流
```

### 职责边界

| 能力 | 归属 | 说明 |
|---|---|---|
| 用户登录/权限/小程序 API | Java | 稳定业务入口 |
| 用户画像/计划/任务/复盘落库 | Java | 关系型数据与事务一致性 |
| Agent 编排/LLM 调用/搜索蒸馏 | Python | FastAPI + LangGraph 状态图 |
| RAG 检索 | Python 调用，Java 管数据 | Java 保存经验和记忆，Python 检索使用 |
| Agent Trace | Python 生成，Java 保存 | 保证调试、回放和质量评估 |
| 内容安全审核 | Java 接入 | LLM 输出先审后发 |
| 后台管理 | Java | 管理用户/计划/任务/来源/经验原子/Agent 运行记录 |

### Java 调 Python 的接口契约

**唯一接口**：`POST /agent/plan/run`

```json
// Request（Java → Python）
{
  "run_id": "string",
  "user_id": "string",
  "message": "string",
  "user_profile": {
    "goal_type": "string",
    "stage": "string",
    "available_time": "string",
    "preference": "string"
  },
  "recent_reviews": [...],
  "memory_summary": "string",
  "history_stats": {
    "recent_completion_rate": "float",
    "common_blockers": [...]
  }
}

// Response（Python → Java）
{
  "run_id": "string",
  "status": "success | fallback | error",
  "final_response": "string",
  "plan": {
    "horizon": "overall | weekly | today",
    "summary": "string",
    "today_tasks": [...]
  },
  "tasks": [
    {
      "title": "string",
      "starter_action": "string",
      "duration": "string",
      "deliverable": "string"
    }
  ],
  "sources": [
    { "url": "string", "title": "string", "source_type": "string", "reliability": "string" }
  ],
  "memory_candidates": [
    { "content": "string", "reason": "string", "sensitivity": "normal | sensitive" }
  ],
  "validation_report": {
    "passed": true,
    "rewrite_count": 0,
    "notes": "string"
  },
  "agent_steps": [
    { "node_name": "string", "latency_ms": 0, "status": "success" }
  ],
  "model_cost": { "tokens_in": 0, "tokens_out": 0, "cost_cny": 0.0 }
}
```

### Trace 串联

- Java 生成 `run_id`（UUID），传给 Python
- Python 每个节点生成 `agent_step`，含 run_id、node_name、input_hash、output_json、latency、error
- Python 返回时附带 agent_steps 摘要
- Java 统一落库到 agent_runs + agent_steps

### 备选方案

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 纯 Python（FastAPI 一套） | 单语言、心智负担低 | 无法展示 Java 工程能力；事务管理不如 Spring | ❌ 放弃（岗位技能需要 Java） |
| 纯 Java（Spring AI） | 单语言 | LangGraph 生态在 Python，Spring AI 不够成熟 | ❌ 放弃 |
| **Java + Python 双服务** | 职责清晰、面试双线展示、模拟大厂架构 | 双服务联调成本 | ✅ 采纳 |

---

## ADR-002：数据模型设计

### 关键数据表

| 表 | 核心字段 | 说明 |
|---|---|---|
| users | id, openid, nickname, created_at | 用户基础信息 |
| user_profiles | user_id, goal_type, stage, available_time, preference, created_at, updated_at | 规划画像 |
| conversations | id, user_id, scene, created_at | 会话 |
| messages | id, conversation_id, role, content, created_at | 消息 |
| plans | id, user_id, horizon, summary, status, created_at | 整体/本周/今日计划 |
| tasks | id, plan_id, title, starter_action, duration, deliverable, status, started_at, completed_at | 今日任务 |
| reviews | id, user_id, plan_id, completion, mood, blocker, adjustment, created_at | 复盘 |
| memories | id, user_id, type, content, confidence, sensitivity, status, is_active, embedding(Vector), created_at | 长期记忆 |
| memory_candidates | id, user_id, content, reason, sensitivity, status, created_at | 待确认记忆 |
| search_sources | id, run_id, url, title, source_type, reliability, fetched_at | 搜索来源 |
| experience_atoms | id, source_ids, conclusion, applicable_user, tasks, risk_note, embedding(Vector), created_at | 经验原子 |
| agent_runs | id, user_id, graph_name, status, model_cost, latency, created_at | 工作流运行 |
| agent_steps | id, run_id, node_name, input_hash, output_json, latency, error, created_at | 节点追踪 |

### 状态枚举

| 实体 | 状态值 |
|---|---|
| plans.status | draft / generated / adopted / in_progress / completed / abandoned |
| tasks.status | pending / in_progress / completed / abandoned / expired |
| memories.status | candidate / confirmed / closed / deleted |
| memories.sensitivity | normal / sensitive |
| agent_runs.status | running / success / fallback / error |

### 向量检索

- 使用 pgvector，memories 和 experience_atoms 表各加 `embedding VECTOR(1024)` 字段（DeepSeek/GLM embedding 维度）
- 检索时用余弦相似度 `<=>` 操作符
- 索引：`CREATE INDEX ON memories USING ivfflat (embedding vector_cosine_ops)`

### 备选方案

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| PostgreSQL + pgvector | 一套库、运维简单、个人项目足够 | 超大规模不如专用向量库 | ✅ 采纳 |
| Qdrant | 专业向量库、性能好 | 多一个组件、多一份运维 | ❌ MVP 不需要 |
| Milvus | 大规模向量 | 过重、2 人团队扛不住 | ❌ 不需要 |

---

## ADR-003：Agent 编排框架

### 决策

采用 **Controlled Agentic Workflow**：外层 Workflow 管流程、状态、权限、日志、停止条件；内层 Agent 在指定节点内完成理解、判断、工具选择、记忆利用、计划生成和校验修正。

### 层级职责

| 层级 | 负责什么 | 不负责什么 |
|---|---|---|
| Workflow | 节点顺序、条件分支、重试、停止、降级 | 不替代 Agent 做语义判断 |
| Agent | 意图理解、信息缺口判断、工具调用决策、经验蒸馏、计划生成 | 不绕过权限、不直接修改业务状态 |
| Harness | 工具边界、上下文、权限、Trace、校验、失败处理 | 不生成用户可见规划内容 |
| Loop | 计划校验失败后的有限修正 | 不做开放式无限循环 |

### 主工作流（11 步）

1. Java 接收小程序请求，读取用户画像、近期任务、复盘和记忆摘要
2. Java 调用 Python Agent 服务，创建一次 plan_run（生成 run_id）
3. 诊断 Agent：判断目标、阶段、焦虑点、缺失信息和是否需要追问
4. 若信息不足，直接返回追问；若信息足够，进入检索与搜索
5. 并行读取用户记忆、历史任务、经验库；必要时并行执行 Web Search
6. 搜索蒸馏 Agent：将搜索结果和经验库内容整理成 experience_atoms
7. 计划 Agent：生成整体规划、本周重点和今日 1-3 个任务
8. 校验陪伴 Agent：检查计划是否可执行、来源是否可靠、话术是否低压力
9. 若校验失败，回到计划 Agent 修改；最多重试 2 次，仍失败则降级为模板计划
10. Python 返回 final_response、plan、tasks、sources、memory_candidates 给 Java
11. Java 保存计划、任务、来源、Agent Trace 和记忆候选，小程序展示结果

### 核心 Agent 定义

| Agent | 目标 | 输入 | 输出 |
|---|---|---|---|
| 诊断 Agent | 识别目标、阶段、缺失信息和是否需要追问 | 用户消息、用户画像、近期复盘 | intent, goal_type, stage, open_questions, search_need |
| 搜索蒸馏 Agent | 判断是否需要搜索，并把来源整理成经验原子 | intent, user_profile, search_results, retrieved_experience | experience_atoms, source_summary, risk_notes |
| 计划 Agent | 生成整体规划、本周重点和今日任务 | user_profile, memory_summary, experience_atoms, history_stats | candidate_plan, today_tasks |
| 校验陪伴 Agent | 检查可靠性、可执行性和情绪负担，生成用户可见表达 | candidate_plan, sources, user_preference | final_plan, companion_message, validation_report |

### 不独立成 Agent 的部分

- 记忆读写：由统一 Memory Service 管理
- 任务状态：由 Java 事务和状态机管理
- 安全审核：规则优先，必要时使用分类模型/LLM 审核函数
- 来源保存和日志：由 Java 统一落库

### 多 Agent 产品设计约束

1. **职责边界清楚**：每个 Agent 只处理一个主要判断任务，不互相覆盖
2. **协作方式结构化**：Agent 通过共享状态对象传递结构化结果，不通过自由对话协作
3. **工具调用受控**：Agent 只能调用已登记工具，涉及用户数据/任务状态/记忆写入必须经过 Java 后端
4. **记忆统一管理**：所有 Agent 统一通过 Memory Service 读写记忆
5. **结果必须可追踪**：每次工作流生成 agent_run，每个节点生成 agent_step
6. **校验与降级优先**：所有用户可见计划必须经过校验，失败最多重写 2 次，仍失败降级模板
7. **不以复杂度证明能力**：MVP 不新增超过 4 个核心 Agent，不做 Swarm/开放自治

### 备选方案

| 框架 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **LangGraph** | 1.x 生产就绪、状态图、Checkpoint、结构化输出、有限 Loop | 与 langchain-core 版本耦合 | ✅ 采纳 |
| CrewAI Flows | 结构化、事件驱动 | 生态不如 LangGraph | ❌ 放弃 |
| AutoGen GraphFlow | 有向图执行 | 发展不确定 | ❌ 放弃 |
| 自研状态机 | 完全可控 | 重复造轮子、2 人团队成本高 | ❌ 放弃 |

### LangGraph 实施要点

- pin 版本 `langgraph==1.2.x` + 配套 `langchain-core`
- 必须接入 LangSmith 做可观测性（调试主要风险）
- 重写循环用 state 计数器字段显式控制（rewrite_count），不只靠 recursion_limit
- State schema 用 TypedDict + Annotated reducer，避免 state 字段被覆盖

---

## ADR-004：LLM 模型选型与分层

### 决策

模型分层策略：诊断/校验用小模型，搜索蒸馏/计划生成用强模型。全部使用国产模型，避免数据出境。

| 节点 | 模型 | 理由 |
|---|---|---|
| 诊断 Agent | DeepSeek 小模型 / GLM-4-Flash | 任务简单（分类+结构化输出），成本低 |
| 搜索蒸馏 Agent | DeepSeek V4 / GLM-4.5 | 需要理解+归纳+结构化 |
| 计划 Agent | DeepSeek V4 / GLM-4.5 | 核心生成任务，质量优先 |
| 校验陪伴 Agent | DeepSeek 小模型 / GLM-4-Flash | 校验+改写话术，任务相对简单 |
| Embedding | DeepSeek Embedding / GLM Embedding | 向量检索用 |
| 内容安全分类 | 微信 msgSecCheck + 自建关键词词表 | 合规要求 |

### 成本估算

| 节点 | 输入 token | 输出 token | 成本估算 |
|---|---|---|---|
| 诊断 | ~1K | ~200 | ¥0.001-0.005 |
| 搜索蒸馏 | ~5K | ~1K | ¥0.02-0.05 |
| 计划生成 | ~4K | ~1.5K | ¥0.02-0.05 |
| 校验陪伴 | ~3K | ~500 | ¥0.003-0.01 |
| 联网搜索 | — | — | ¥0.03-0.1 |
| **合计** | | | **¥0.08-0.2 / 次** |

### 备选方案

| 模型 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **DeepSeek V4** | 便宜、推理强、Agent 能力好 | 偶发限流 | ✅ 主选 |
| 智谱 GLM-4.5/4.6 | 稳定、合规友好 | 略贵 | ✅ 备选 |
| 通义 Qwen3 | 通用强 | 价格中等 | 🟡 兜底 |
| OpenAI GPT-4o | 效果好 | 贵 + 数据出境合规问题 | ❌ 不用 |

---

## ADR-005：RAG 与联网搜索

### 记忆原则

- 多 Agent 不各自维护私有记忆，统一通过 Memory Service 读取和写入
- 结构化记忆进入关系型数据库，语义记忆进入向量检索存储
- 普通记忆可自动写入；敏感记忆需要用户确认
- 用户可查看、删除、关闭长期记忆

### RAG 使用边界

| RAG 类型 | 检索内容 | 用途 |
|---|---|---|
| 用户记忆 RAG | 该用户目标、偏好、复盘和行为模式 | 个性化规划 |
| 经验知识 RAG | 系统沉淀的考公、央国企、AI 求职经验原子 | 经验借鉴 |

RAG 不替代联网：RAG 用于稳定经验，联网用于最新公告、岗位、政策、招聘信息。

### 联网搜索规则

- 联网搜索只由搜索蒸馏 Agent 或校验节点按需触发，计划 Agent 不直接联网
- 涉及最新招聘、考试、报名等动态事实时必须联网核查
- 用户可见回复中涉及动态事实必须展示来源提示或"以官方公告为准"
- 经验帖只能作为经验参考，不能被写成确定结论

### 备选方案

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **Tavily API** | 有免费额度、结构化来源、AI 友好 | 海外服务 | ✅ 主选（面试阶段） |
| Serper | 便宜、Google 结果 | 需自行解析 | 🟡 备选 |
| DeepSeek 原生联网 | 无需额外接入 | 不可控、无结构化来源 | ❌ 不做主方案 |
| Bing Search API | 微软生态 | 贵、停服风险 | ❌ 放弃 |

### 经验原子冷启动

- MVP 阶段人工整理 30-50 条高质量经验原子，覆盖三首发场景
- 每条含：source_ids（来源链接）、conclusion（结论）、applicable_user（适用人群）、tasks（推荐任务）、risk_note（风险提示）
- 后续可做半自动抽取：强模型从经验帖抽取 → 人工审核入库

---

## ADR-006：部署方案

### 决策

Docker Compose 单机部署，所有服务跑一台服务器。

### docker-compose 服务

| 服务 | 镜像 | 端口 | 依赖 |
|---|---|---|---|
| nginx | nginx:alpine | 443, 80 | java, python |
| java | 自建 JDK17 镜像 | 8080 | postgres, redis |
| python | 自建 Python3.11 镜像 | 8000 | postgres, redis |
| postgres | postgres:16 + pgvector | 5432 | — |
| redis | redis:alpine | 6379 | — |

### 服务器配置

| 阶段 | 配置 | 预算 |
|---|---|---|
| 面试 demo | 4C8G 云服务器 | ¥100-200/月 |
| 上线后 | 8C16G + 独立 PostgreSQL | ¥300-500/月 |

### 备选方案

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **Docker Compose 单机** | 简单、一键起、2 人可控 | 单点 | ✅ MVP 采纳 |
| K8s | 可扩展、大厂标准 | 过重、2 人扛不住 | ❌ MVP 不需要 |
| 云函数 + 托管数据库 | 免运维 | 冷启动、LangGraph 长任务不友好 | ❌ 放弃 |

---

## ADR-007：内容安全与安全规则清单

### 内容安全审核架构

```
用户输入 → Java 接收 → msgSecCheck 审核输入
                          ├─ 违规 → 拒绝处理，返回提示
                          └─ 通过 → 调 Python Agent
                                       → LLM 生成输出
                                       → 返回 Java
                                       → msgSecCheck 审核输出
                                            ├─ 违规 → 触发重写或模板降级
                                            └─ 通过 → 返回小程序
```

### 高风险关键词词表（初版，需持续补充）

- 心理危机：自杀、自残、自伤、想死、活不下去、轻生、不想活了、结束一切
- 医疗：诊断、抑郁症、焦虑症、吃药、副作用、处方
- 法律：官司、诉讼、律师、判决、维权（仅触发分流，不拦截内容）
- 金融：股票、基金、理财、投资、收益、保本（触发分流，不做投资建议）

### 心理危机固定话术

> 我能感受到你现在很难受。你现在不是一个人，我们一起找能帮到你的人。
>
> 请立即拨打全国心理援助热线 **12356**，或者联系身边信任的人。
>
> 如果你觉得自己有立即的危险，请拨打 120 或 110。

### 高风险触发后行为

1. 停止普通规划流程
2. 返回固定话术（不交给 LLM 自由生成）
3. 不进入记忆候选
4. 后台记录安全分流事件（脱敏存储）
5. 无法判断风险等级时采用保守提示

---

## ADR-008：开发拆解与里程碑

### 4 周开发计划

| 周次 | 目标 | 交付物 | 验收 |
|---|---|---|---|
| 第 1 周 | 基础设施 + 端到端链路 | 数据库表结构（13 表）、基础 API（CRUD）、FastAPI 骨架、Java 调 Python 链路打通、小程序 5 页面骨架 | 端到端 1 次请求打通（哪怕返回 mock 数据） |
| 第 2 周 | 最小 Agent 工作流 | PlanState、4 节点状态图、结构化输出、计划校验和有限重写、单场景跑通 | 计算机求职场景 1 次 plan_run 成功 |
| 第 3 周 | 工具/RAG/联调 | Web Search、RAG Retriever、经验蒸馏、来源保存、三场景覆盖、小程序联调 | 三场景各有 1 次 plan_run 成功 |
| 第 4 周 | 质量与完整工程 | Trace 后台、降级策略、固定评测集（15 case）、单元测试、Docker Compose 部署、API 文档 | 全部降级路径有测试、Docker Compose 一键起 |

### 评测集设计

| 场景 | Case 数 | 覆盖 |
|---|---|---|
| 计算机求职 | 5 | 正常规划/信息追问/来源不足/校验失败/通用场景 |
| 考公准备 | 5 | 同上 |
| AI/后端实习 | 5 | 同上 |
| **合计** | **15** | 含正常 + 异常路径 |

---

## ADR-009：三层架构——通用-场景分离原则

### 决策

采用 **L1 能力层 / L2 流程层 / L3 场景层** 三层分离，L1/L2 场景无关，L3 数据驱动。

### 层级职责

| 层 | 职责 | 场景相关性 | 示例 |
|---|---|---|---|
| **L1 能力层** | 记忆 / 检索 / 搜索 / 状态机 / 合规适配 | 场景无关 | MemoryService、PlanStateMachine、SafetyFilter |
| **L2 流程层** | Agent 工作流编排：诊断 → 蒸馏 → 计划 → 校验 | 场景无关 | 4 个 Agent 节点、Run 聚合、PlanState |
| **L3 场景层** | 场景配置 / 经验原子 / prompt few-shot / starter_action 模板 | **场景化** | `cs_job_seeking`、`civil_service` 的经验原子、prompt 配置 |

### 设计不变量

- I-1：诊断 Agent 不认识"求职/考公"具体业务，只输出 `scene_tag`
- I-2：计划 Agent 只认 `user_profile + experience_atoms`，不知道 atom 来自哪个领域
- I-3：新增一个场景 = 灌一批 L3 数据 + 改一组 prompt，**不改 L1/L2 代码**
- I-4：未支持场景走 `scene_tag=general` 兜底

### 备选方案

| 方案 | 结论 | 理由 |
|---|---|---|
| 三层分离（L1/L2 场景无关） | ✅ 采纳 | 通用设计与 MVP 单场景兼顾 |
| 三场景硬编码 | ❌ 放弃 | 陷进耦合、扩展成本高 |
| 先做三场景再抽象 | ❌ 放弃 | 标注成本高、代码反复改造 |

---

## ADR-010：长任务 API 形态（POST 创建 + SSE 流）

### 决策

规划生成接口从同步阻塞改为**长任务 API**：POST 创建返 task_id + GET SSE 订阅事件。

### 接口形态

```
POST /agent/plan/run
  → 返回 { run_id, status: QUEUED }
GET /agent/plan/run/{run_id}/events
  → SSE 流，事件类型：PLAN_STARTED / DIAGNOSIS_DONE / DISTILL_DONE
    / PLAN_DONE / VALIDATION_PASSED / VALIDATION_REWRITE / COMPLETED / FAILED
POST /agent/plan/run/{run_id}/cancel
  → 中断运行
```

### Run 状态机（Agent 侧）

```
QUEUED → PLANNING → RESEARCHING → DRAFTING → REVIEWING → DELIVERED
                         ↑__________________________________|
                                  (REVIEW 不通过可回退)
任意状态 → CANCELLED（用户中断）
任意状态 → FAILED（降级终态）
```

### 备选方案

| 方案 | 结论 | 理由 |
|---|---|---|
| 异步任务 + SSE（Coze/Dify 形态） | ✅ 采纳 | 行业标准、用户可观察进度、可中断 |
| 同步阻塞 POST | ❌ 放弃 | 8-20s 用户等待焦虑、无法中断、网关超时风险 |
| WebSocket 双向 | ❌ 放弃 | MVP 不需要双向，SSE 更轻 |

---

## ADR-011：限流 + 重试 + 熔断策略

### 决策

对 LLM API 和搜索 API 强制实现**限流 + 重试 + 熔断**，这是 agent 项目的工程底线。

### 实现层位

| 策略 | Python Agent 侧 | Java 业务侧 |
|---|---|---|
| LLM 调用重试（tenacity 指数退避，3 次） | ✅ 各 Agent 节点 | — |
| 搜索 API 重试（同上） | ✅ 蒸馏节点 | — |
| 令牌桶限流（`aiolimiter`） | ✅ 对 LLM 每秒 N 次、对搜索每分钟 M 次 | — |
| 总入口限流（用户级） | — | ✅ Spring Security + Redis 计数 |
| 熔断 Java → Python | — | ✅ 连续失败 N 次进入熔断态，T 秒内直接走模板降级 |

### 备选方案

| 策略 | 结论 | 理由 |
|---|---|---|
| 必须 tenacity + aiolimiter + Resilience4j | ✅ 采纳 | 行业标准、实现成本低 |
| 不做（"面试阶段日活低"） | ❌ 拒绝 | 不是工程项目、面试常问工程问题 |

---

## ADR-012：Token 预算与 Context 工程

### 决策

PlanState 增加 `token_budget` 字段；搜索蒸馏结果用摘要回填，不直接塞原文。单任务超额则标记 `degraded=true`。

### 设计要点

- PlanState：`token_budget: int = 8192`（默认值，可配置）
- 搜索蒸馏 Agent 输出前先做摘要，控制回填到上下文的长度
- sources 数组超额时截断 + 标记 `degraded=true` + 提示用户"本次检索不足"
- 不变量：单任务总 token 必须低于预设上限

### 备选方案

| 方案 | 结论 | 理由 |
|---|---|---|
| 硬上限 + 摘要回填 | ✅ 采纳 | 实现简单、防成本失控 |
| 复杂预算算法（按节点分配权重） | ❌ 后置 | MVP 不必要 |

---

## ADR-013：MCP 工具实现

### 决策

将 Web Search 工具实现为一个 **stdio MCP server**，Python Agent 通过 MCP 协议调用。

### 设计要点

- 在 `dazi-agent/infrastructure/mcp/search_server.py` 实现 stdio MCP server
- 暴露 `web_search(query, max_results) → [{url, title, snippet}]`
- README 增加"兼容 MCP 协议"说明
- 2025 加分项：OpenAI Agents SDK / Google ADK 均已内置 MCP 支持

### 备选方案

| 方案 | 结论 | 理由 |
|---|---|---|
| Web Search 实现为 MCP server（哪怕 stdio） | ✅ 采纳 | 行业加分项、与 OpenAI SDK / ADK 趋势一致 |
| Web Search 写成普通 function tool | ❌ 放弃 | 失去 2025 关键技术信号 |

---

## ADR-014：技术评估报告——纯 Python vs Java+Python 双服务

### 决策

采用 **Java（Spring Boot）业务后端 + Python（FastAPI + LangGraph）Agent 服务** 双服务架构。

本 ADR 同时作为技术评估报告，沉淀针对本项目具体需求的方案对比与选型依据。所有结论基于对 13 项 PRD 功能需求的逐项评估，不基于泛化判断。

### 1. 评估前提：产品形态判别

技术选型前必须先判别"产品形态"，不同形态对应不同最优解。

**判别模型**：AI 在产品中的占比决定主轴。

| 形态 | 特征 | 典型项目 | 推荐架构 |
|---|---|---|---|
| **AI 主导，业务是载体** | 没有 AI 这个产品就不存在；agent loop 是主轴 | ChatGPT、Claude Code、Devin、Manus | 纯 Python / TS |
| **业务主导，AI 是能力** | 没有 AI 产品依然成立；业务流程（用户/订单/任务/合规）是主轴 | 银行 +AI 客服、淘宝 +AI 推荐、ERP +AI 预测 | **Java/Go 业务 + Python AI** |
| **平衡型** | AI 与业务各占一半 | Dify、Coze 后端 | 纯 Python（FastAPI 一套） |

**"把 LLM 换成随机函数，产品还剩什么？"** 判别小测试：

| 答案 | 项目类型 |
|---|---|
| 剩下完整建档/任务/复盘/记忆/合规流程，AI 只是质量下降 | **业务主导** |
| 剩下一个用户/任务/复盘的小程序，但用户可能不再来 | 平衡型 |
| 剩下一个空 UI，什么都没有 | AI 主导 |

### 2. 本项目的形态判定

针对本项目的 6 个上下文做评估：

| 上下文 | 是业务吗 | 没 LLM 还在吗 |
|---|---|---|
| 建档 Profile | ✅ 纯业务（表单 + 校验） | ✅ 在 |
| 任务 Task | ✅ 纯业务（CRUD + 状态机） | ✅ 在 |
| 复盘 Review | ✅ 纯业务（表单 + 统计） | ✅ 在 |
| 记忆 Memory | ✅ 纯业务（KV + 敏感分级 + 审计） | ✅ 在 |
| 安全 Safety | ✅ 纯业务（关键词 + 固定话术 + 合规） | ✅ 在 |
| 规划 Planning | ⚠️ 半业务半 AI | ❌ 只剩模板降级 |

**结论：6 个上下文里 5 个是纯业务，项目属于"业务主导，AI 是能力"形态。Java 业务后端 + Spring 事务/权限/审计/状态机的价值最大化；Python Agent 是"能力接入"。**

### 3. 评估维度：13 项 PRD 功能逐项对比

对比颗粒度：基于 PRD 具体功能，看两种方案实现差异。

| # | PRD 功能（章节） | 纯 Python 适合度 | Java+Python 适合度 | 谁更适合 |
|---|---|---|---|---|
| 1 | 用户建档（6.2） | ⚠️ 表单+校验要自写 | ✅ Spring 表单+校验+事务 | **Java** |
| 2 | 规划生成（6.3，含 Agent） | ✅ LangGraph 母语 | ✅ Python 部分 | **平**（都靠 Python Agent） |
| 3 | 任务状态机（6.4） | ⚠️ 手写状态变更 | ✅ 充血模型 + ArchUnit | **Java 完胜** |
| 4 | 每日复盘（6.5） | ⚠️ 表单 + 统计要自写 | ✅ 事务一致性 + 审计 | **Java** |
| 5 | 记忆管理 + 敏感分级（6.6） | ⚠️ 自研合规 | ✅ AOP + 注解 + 脱敏 | **Java 完胜** |
| 6 | 安全分流（6.7） | ⚠️ 手写规则引擎 | ✅ 注解 + Filter | **Java** |
| 7 | 后台管理（6.8） | ⚠️ 自写 admin | ✅ Spring Boot Admin 现成 | **Java 完胜** |
| 8 | 内容审核（先审后发） | ⚠️ 手写 middleware | ✅ Servlet Filter + AOP | **Java** |
| 9 | 算法备案/合规 | 都需要 | 都需要 | 平 |
| 10 | LLM 成本/限流/重试 | ✅ tenacity/aiolimiter | ✅ 同（Python 侧） | 平 |
| 11 | Agent 编排（4 节点） | ✅ LangGraph 母语 | ✅ 同（Python 侧） | 平 |
| 12 | Token 预算/Context 工程 | ✅ 在 Python 侧 | ✅ 同 | 平 |
| 13 | MCP 工具 | ✅ Python MCP SDK | ✅ 同 | 平 |

**汇总：13 项需求里，6 项 Java 完胜，7 项打平，0 项纯 Python 完胜。**

### 4. 核心差距源：三大典型差异

**差异 1：业务事务一致性**

| 需求 | 纯 Python | Java+Python |
|---|---|---|
| 任务状态机流转（不可回退） | 手写 transit_to 方法，靠自律调用 | 充血聚合根 `Task.transitTo()` + ArchUnit 禁止 `setStatus()` |
| 乐观锁（多用户并发改计划） | 手写 version 字段 + retry | `@Version` 一行注解 |
| 跨表事务（plan + tasks + agent_steps） | 手动 commit/rollback | `@Transactional` 声明式 |

**差异 2：合规与审计**

| 需求 | 纯 Python | Java+Python |
|---|---|---|
| 审计日志（15 工作日 SLA） | 手写 middleware | Spring AOP + `@Audited` 注解 |
| 敏感字段脱敏 | 手写字段 mask | `@MaskedField` 注解驱动 |
| 删除权追溯 | 难（数据散在 logs） | 完整审计表 + 关系链 |

**差异 3：状态机 + 聚合根**

| 需求 | 纯 Python | Java+Python |
|---|---|---|
| 状态变更强制守护 | ❌ 无编译期守护 | ✅ ArchUnit |
| DDD 聚合根充血模型 | ⚠️ 别扭（Python 偏函数式） | ✅ Spring + COLA 成熟 |

### 5. 候选方案对比

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| **A：Java + Python 双服务** | 业务能力（事务/权限/审计）成熟；Java 后端岗位求职信号强；DDD 落地优雅 | 双服务联调成本；部署成本；双语言心智 | ✅ **采纳** |
| **B：纯 Python（类 Dify/Coze 路线）** | 单语言心智低；agent 生态母语；部署简单 | 业务事务/权限/审计要自研；失去 Java 后端岗位信号；DDD 落地别扭 | ❌ 放弃 |
| **C：纯 Java（Spring AI / LangChain4j）** | 单语言；Spring 生态成熟 | LangChain4j/Spring AI 生态弱于 LangGraph；失去 agent 岗位语言优势 | ❌ 放弃 |
| **D：JS/TS 全栈（Vercel AI SDK）** | 前后端统一 | 后端业务能力弱；Java 岗位完全无关 | ❌ 放弃 |

### 6. 决策理由

基于上述评估，本项目选 Java + Python 双服务，理由按优先级排序：

1. **项目形态是"业务主导"**：6 上下文中 5 个是纯业务。Java Spring 在事务/权限/审计/状态机上的成熟度最大化。
2. **岗位覆盖最广**：Java 后端岗 / AI Infra 岗 / Agent 应用岗三线皆可命中。
3. **Agent 能力无损失**：Python Agent 侧的 LangGraph / RAG / MCP / 限流 / Context 工程全部保留。
4. **可演进性**：双服务天然为未来拆物理服务留接口。

### 7. 风险与缓解

| 风险 | 等级 | 缓解策略 |
|---|---|---|
| 双服务联调成本 | 中 | 接口契约先行 + `check-contract.sh` 校验 Schema 一致性；Mock 对接 |
| 部署复杂度高 | 中 | Docker Compose 一键启动；CI 流水线统一编排 |
| 团队扩展只懂一种语言 | 低 | 文档齐全；AI 工具降低跨语言成本；未来招人按语言分岗 |
| Java 侧调 Python 超时 | 中 | 熔断（Resilience4j）+ 模板降级；详见 ADR-011 |
| 双语言调试链路长 | 中 | `run_id` 全链路 Trace；LangSmith + Spring Actuator 双端可观测 |

### 8. 与 Claude Code 类项目的差异澄清

| 维度 | Claude Code 类 | 本项目 |
|---|---|---|
| 产品形态 | 开发者工具（CLI/IDE 插件） | C 端多用户业务产品 |
| 用户数 | 1 个程序员 | 多用户并发 |
| 持久化 | 文件系统（可丢） | 数据库（长期保存 + 合规） |
| 业务对象 | 文件、对话 | 用户/画像/计划/任务/复盘/记忆 |
| 事务一致性 | 不重要 | 重要（状态机 + 乐观锁 + 审计） |
| 合规要求 | 几乎为零 | 算法备案 + 敏感信息保护 |
| 状态机 | tool call 状态（简单） | 任务/计划/记忆 3 个状态机 |

**结论**：Claude Code 类项目适合纯 Python，是因为它没有业务；本项目与之不是同一形态。本项目更接近"CRM + AI 客户洞察"、"教育系统 + AI 个性化"，因此 Java 业务后端是必要而非可选。

### 9. 可借鉴部分（纯 Python agent 的设计精华）

虽然整体架构选择 Java+Python，但 Claude Code / Dify 这类纯 Python agent 项目的**harness 内部设计**值得借鉴到本项目的 Python Agent 侧：

| 借鉴源 | 借鉴内容 | 落地到本项目 |
|---|---|---|
| Claude Code | 受控 tool loop、tool 白名单、超时 | 4 个 Agent 节点的工具白名单 spec |
| Claude Code | 上下文压缩（防爆 token） | ADR-012 Token 预算与摘要回填 |
| Claude Code | 可中断 + 续跑 | ADR-010 长任务 API + Run 状态机 |
| Dify | POST → task_id → SSE 事件流 | ADR-010 接口形态 |
| OpenAI Agents SDK | Trace span 命名规范 | agent_steps 表的 trace 命名 |
| LangGraph | StateGraph + Checkpoint | 直接复用 LangGraph（已在用） |

### 10. 结论

> **本项目是"业务主导，AI 是能力"形态，6 个上下文中 5 个是纯业务。Java Spring 在事务/权限/审计/状态机上的成熟度远超 Python 自研，因此 Java 业务后端 + Python Agent 服务的双服务架构是针对本项目具体需求的最优解。纯 Python 方案在 Agent 编排能力上与双服务架构打平，但在业务事务/合规/状态机维度完败。**

---

## 参考技术依据

- LangGraph 官方文档：长运行、有状态 Agent，持久化、人工介入、记忆和调试能力（v1.2.x 生产就绪）
- CrewAI Flows 官方文档：结构化、事件驱动工作流（已对比，未采用）
- AutoGen GraphFlow 官方文档：有向图执行和结构化控制（已对比，未采用）
- OpenAI Agents SDK 官方文档：工具调用、Guardrails、Sessions、Tracing（参考设计）
- OpenAI Web Search 官方文档：web_search 来源、上下文大小、域名过滤（参考设计）
- pgvector 官方文档：ivfflat 索引、余弦相似度检索
- Google ADK 官方文档（adk.dev，2025）：支持 Python / Java / Kotlin / Go / TypeScript；MCP / A2A 内置；context compaction；sessions；evaluation
- OpenAI Agents SDK 官方文档（2025）：Python-first；Agent Loop；Handoffs；Sandbox；Tracing；Session；Guardrails（并行 fail-fast）
- Anthropic《Building Effective Agents》（2024.12）：orchestrator-worker 等 5 种模式
- Dify 架构调研（github.com/langgenius/dify）：TypeScript + Python 双语言；POST → task_id → SSE 形态
- Spring AI 官方文档（spring.io/projects/spring-ai，2025 1.0 GA）：ChatClient、Advisors、结构化输出、可观测性
- LangChain4j 官方文档（docs.langchain4j.dev）：Java 版 LangChain；Spring Boot/Quarkus 集成
- 阿里 COLA 4.x（github.com/alibaba/COLA）：Clean Object-Oriented and Layered Architecture；四层折中实践
- 美团技术博客《DDD 在大众点评交易系统演进中的应用》（2024.05）：大厂 DDD 实战案例
