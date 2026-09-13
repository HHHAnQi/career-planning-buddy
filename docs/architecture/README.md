# 架构文档入口

本目录定义跨模块技术事实。独立项目边界和最终实现优先级见 [project-baseline](../implementation/project-baseline.md)。

| 文档 | 用途 |
|---|---|
| [adr.md](./adr.md) | 记录不可轻易变更的架构决策 |
| [bounded-context-map.md](./bounded-context-map.md) | DDD 子域、模型所有权、Context Map 与依赖规则 |
| [tdd.md](./tdd.md) | 后端、前端、Agent Runtime 的实施设计 |
| [api-and-data-contracts.md](./api-and-data-contracts.md) | 通用 API、状态机、SSE 和 Schema 契约 |
| [technology-decision-matrix.md](./technology-decision-matrix.md) | 当前做、延后做、不做的技术清单 |
| [cron-and-workers.md](./cron-and-workers.md) | Agent lease worker、超时接管和定时任务 |
| [poc-verification-report.md](./poc-verification-report.md) | 真实模型接入前的 smoke checklist |
| [ai-scenario-and-risk-analysis.md](./ai-scenario-and-risk-analysis.md) | AI 适用边界与风险控制 |

架构事实：FastAPI 单体、React SPA、PostgreSQL + pgvector、单核心 Agent、三类 Provider；
Agent Run 使用数据库 lease，Eval 仍受单 Worker 约束，无 ClawAgent 依赖。
