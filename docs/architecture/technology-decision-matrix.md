# 技术点决策矩阵

## 现在采用

| 领域 | 决策 | 原因 |
|---|---|---|
| 后端 | FastAPI + Python 3.12 | 异步模型调用和快速开发 |
| ORM | SQLAlchemy 2 Async + asyncpg | 类型清晰、支持事务与异步 |
| 数据库 | PostgreSQL 16 | 业务、Trace 和事件统一存储 |
| 向量 | pgvector | 个人项目规模足够，减少组件 |
| Agent | LangGraph + 单核心 Agent | 条件工作流可追踪，不引入多 Agent |
| 实时 | SSE | 单向进度和结果推送足够 |
| 前端 | React + TypeScript + Vite | 生态成熟、演示方便 |
| 查询状态 | TanStack Query | 服务端状态管理 |
| 本地 UI 状态 | React state，必要时 Zustand | 避免过早全局状态 |
| 鉴权 | Guest JWT | MVP 用户隔离，低注册摩擦 |
| 模型 | OpenAI-compatible Provider | 可按配置切换实际可用模型 |
| 搜索 | SearchProvider | 可 Mock，可后接 Tavily/其他服务 |
| 测试 | pytest + httpx + Vitest | 覆盖后端分层和前端组件 |
| 部署 | Docker Compose | 一键演示 |

## 延后采用

| 技术 | 触发条件 |
|---|---|
| Redis / Celery | 多 Worker、可靠重试、任务积压成为真实需求 |
| Kafka / RabbitMQ | 高吞吐事件流或跨服务异步 |
| 独立向量库 | 向量规模和 P95 超出 pgvector 能力 |
| 对象存储 | Trace Artifact、上传文件或媒体量显著增加 |
| WebSocket | 需要真正双向实时协作 |
| OAuth | Guest 模式验证产品价值后 |
| LangSmith | 团队接受外部 SaaS 且需要托管 Trace |
| 多 Agent | 出现可证明的角色隔离和并行收益 |
| MCP | 需要对外开放工具生态，不是为了追热点 |
| Kubernetes | 单机部署无法满足可用性或扩容需求 |

## 明确不做

- 以 ClawAgent 为底座；
- 为了“架构高级”同时引入 Redis、MongoDB、Milvus；
- 模型直接写数据库；
- 让前端传 user_id 决定数据归属；
- 把每个普通函数命名为 Agent；
- 将 Codex 或任何编码助手硬编码为运行时模型。
