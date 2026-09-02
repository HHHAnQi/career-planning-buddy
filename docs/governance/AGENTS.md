# AI 协作补充说明

根目录的 [AGENTS.md](../../AGENTS.md) 与 [AGENTS.zh-CN.md](../../AGENTS.zh-CN.md) 是编码助手的权威入口。本文件只补充阅读方法。

## 项目身份

- 独立的 AI 求职规划产品；
- FastAPI 单体后端 + React SPA；
- 只有一个具备工具选择能力的 `CareerPlanningAgent`；
- 其他 LangGraph 节点用于规则、上下文、校验、持久化和安全分流；
- 当前仓库是设计包，不得假装已有代码或测试结果；
- 不以 ClawAgent 为底座。

## 最小阅读路径

任何编码任务先读：

1. 根 `AGENTS.zh-CN.md`；
2. `docs/implementation/project-baseline.md`；
3. 当前 Stage 任务书；
4. 该任务涉及的 API、数据表、节点和状态机 spec。

## 行为要求

- 先输出影响文件和实现计划，再改代码；
- 新增字段、状态、端点前先更新正式 spec；
- 不自行引入 Redis、Celery、MCP、多 Agent 或微服务；
- 不把编码助手名称写成运行时模型依赖；
- 不宣称未执行的测试已经通过；
- 遇到文档冲突，按根 README 的权威顺序处理并指出冲突。
