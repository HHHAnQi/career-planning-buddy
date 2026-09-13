# 健康与就绪探针

这些端点不要求 JWT，也不调用 LLM、Search 或 Embedding 的外部 API。

| 方法与路径 | 用途 | 成功 | 失败 |
|---|---|---|---|
| `GET /health` | 兼容既有客户端 | `200` | — |
| `GET /health/live` | 判断进程是否存活 | `200` | 进程不可用时无响应 |
| `GET /health/ready` | 判断实例是否可接收业务流量 | `200` | `503` |

`/health/ready` 返回 `database`、`migrations`、`providers` 三项检查。数据库查询有 2 秒
上限；迁移版本必须等于当前应用的唯一 Alembic head；Provider 只校验配置完整性，
Mock 警告不会导致开发或测试环境不就绪。响应只包含 Provider 名称、缺失字段和警告，
不得包含密钥、Base URL 或模型名。

部署平台应使用 `/health/live` 进行存活判断，使用 `/health/ready` 控制流量接入。
