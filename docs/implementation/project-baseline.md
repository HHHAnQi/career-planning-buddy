# 项目实现基线

| 项目 | 决策 |
|---|---|
| 项目形态 | 独立 FastAPI 单体 + React SPA |
| 与 ClawAgent 关系 | 无代码依赖、无运行时依赖、无数据库依赖 |
| Agent | 1 个 `CareerPlanningAgent` + 受控节点 + 统一 Runtime/Harness |
| 数据库 | PostgreSQL 16 + pgvector |
| 异步执行 | Agent Run 使用 PostgreSQL claim/lease/heartbeat；本地 Task 只做执行句柄 |
| SSE | `agent_events` 作为断线续传事实源；heartbeat 不持久化 |
| Provider | LLM / Search / Embedding 三类 Protocol |
| 运行时模型 | 统一 LLM 契约 + Provider 能力适配；当前 wire protocol 为 OpenAI Chat-compatible |
| 编码助手 | 使用 Codex；只参与开发，不写入运行时架构依赖 |
| 任务队列 | 不引入 Redis/Celery；Agent Run 由 PostgreSQL lease 队列调度，Eval 仍限制单 Worker |
| 部署 | Docker Compose 单机 |

## 1. 核心用例

```text
Guest 登录 → 选择开始/结束日期并建档 → 创建 Goal Brief → 补充信息并确认 → 创建 Agent Run → SSE 查看进度
→ 获得多周方向与固定 7 天执行周期 → 完成当天任务 → 提交每日复盘并调整本周待办
→ 周周期结算 → 用户确认 → 生成下一周；重大方向变化才提前产生新计划版本
```

## 2. 后端分层

```text
api        HTTP、JWT、SSE、错误映射
schemas    Pydantic DTO、枚举
services   用例、事务、状态机、幂等
repositories SQLAlchemy 查询与持久化
agent      LangGraph、节点、状态对象
providers  LLM/Search/Embedding 外部适配
harness    Trace、预算、事件、Eval
core       配置、日志、数据库、通用异常
```

依赖方向：

```text
api → services → repositories
           └→ agent → providers
           └→ harness
```

Agent 节点不得直接依赖 ORM Model。

## 3. MVP Agent Graph

核心节点：

1. `risk_gate`
2. `intent_router`
3. `clarification`
4. `context_builder`
5. `career_planning_agent`
6. `rule_validator`
7. `revise_or_fallback`
8. `companion_response`
9. `persist`
10. `safe_response`（风险分支）

增强节点：

- `quality_reviewer`：Stage 5 再启用；
- `distill_evidence`：Stage 4 再启用。

## 4. 状态机

### AgentRunStatus

```text
pending → running → completed
                 ↘ degraded
                 ↘ failed
pending/running → cancelled
```

### PlanStatus

```text
generated → active → completed → archived
generated/active → archived
```

`adopted_at` 是时间字段，不是状态。用户首次开始任务时，计划从 generated 进入 active。

### TaskStatus

```text
pending → in_progress → completed
pending/in_progress → abandoned
pending → expired
completed → in_progress（仅当前未归档周期内由用户撤销误操作）
```

## 5. 运行时限制

- 单用户同一时刻最多 1 个 pending/running Run；
- 主 Agent 最多 2 轮 Tool Calling、每轮最多 2 个、总 Tool 调用最多 4；
- Stage 2/3 单 Run最多 5 次 LLM；Stage 4 最多 7 次；Stage 5 仅在线 enforce reviewer 时最多 8 次；
- 单 Tool 默认超时 8 秒；
- 单 Run 默认截止时间 45 秒；
- 单 Run 默认总 Token 预算 16000，单次输入 6000、输出 1500（均可配置）；
- 结构化格式失败最多修复 1 次；业务规则失败最多使用专用 repair Prompt 修复 1 次；
- 计划严格位于用户必填的开始/结束日期闭区间（最多 8 周）；当前执行层通常展开固定 7 天周期，最终周期可为剩余 1~7 天，每天 1 个关键任务，不因每日完成而滚动补位；每日预计时长不得超过用户预算；
- `今天` 页面只承载当天任务、当天进度与复盘入口；固定七天安排、周重点和总体方向统一在 `路线` 页面查看，避免两个页面重复表达规划信息；
- 取消和超时必须通过统一 Finalizer 写唯一最终状态及事件；
- Run 冻结 graph/config snapshot，context_builder 后冻结 input snapshot；
- 终态 result_kind 为 plan/clarification/safe_response/navigation；failed/cancelled 无结果。

## 6. 数据表基线

业务表：

- users
- user_profiles
- plans
- tasks
- reviews
- memories
- memory_candidates
- search_sources
- experience_atoms
- companion_messages
- goal_briefs

运行表：

- agent_runs
- agent_steps
- tool_calls
- agent_events

Eval 第一版使用仓库内 JSONL 数据集；实验结果可写 `eval_experiments`，不是 Stage 1 阻塞项。

## 7. 环境变量

环境字段、默认值和组合校验以根目录 `.env.example` 与后端 `Settings` 为唯一事实源，
不得在文档中维护第二份易漂移的变量清单。配置变更后必须执行
`cd backend && python -m scripts.audit_config`。Provider 模式、必填字段和部署方法见
[Provider 配置与部署](../third-party-integration/provider-configuration.md)。

运行时 Provider 由应用级 Registry 统一构建，HTTP 服务与 Agent Tool 复用同一组实例；
禁止在路由依赖中临时读取环境变量或重复创建 Provider。

基础设施探针约定：

- `/health`：兼容旧客户端的浅层存活检查；
- `/health/live`：只检查进程是否存活；
- `/health/ready`：检查 PostgreSQL、Alembic head 和脱敏 Provider 配置，不调用计费 API。

## 8. 明确不做

MVP 不做：

- 复用 ClawAgent；
- 多 Agent；
- MCP 工具市场；
- Redis/Celery/Kafka；
- 微服务拆分；
- 多 Worker 可靠调度；
- 复杂 OAuth；
- 主动消息推送；
- 生产级心理健康服务。

多 Worker 可靠调度、集中式 Secret 管理、真实 Search/Embedding 上线等生产增强项，
必须按[生产就绪审查](../review/production-readiness-audit-2026-08-10.md)逐项验收，
不得仅通过配置开关宣称完成。
