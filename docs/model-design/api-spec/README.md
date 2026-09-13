# API 端点施工索引

通用约定见 [API 与数据契约](../../architecture/api-and-data-contracts.md)。本目录定义单个 Router、Request、Response 和错误路径。

| 资源 | spec |
|---|---|
| 健康与就绪探针 | [health.md](./health.md) |
| Guest 登录与当前用户 | [auth.md](./auth.md) |
| 用户画像 | [profile.md](./profile.md) |
| 求职材料 | [resumes.md](./resumes.md) |
| Agent Run 与 SSE | [agent-runs.md](./agent-runs.md) |
| 计划 | [plans.md](./plans.md) |
| 任务 | [tasks.md](./tasks.md) |
| 复盘与重规划 | [reviews.md](./reviews.md) |
| 记忆 | [memories.md](./memories.md) |
| 澄清事件 | [clarification.md](./clarification.md) |
| 错误码 | [errors.md](./errors.md) |
| 开发者 Trace | [dev-runs.md](./dev-runs.md) |
| Eval | [dev-evals.md](./dev-evals.md) |

除根级健康探针外，所有资源路径都在 `/api/v1` 下。身份只从 JWT 获取，不接受请求体 user_id。
