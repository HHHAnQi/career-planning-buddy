# Contributing

感谢你关注 Career Planning Buddy。这个仓库采用契约优先、单纵切交付和 Mock 优先验证，贡献应保持现有边界，而不是把项目扩展成通用 Agent 平台。

## 开始之前

1. 阅读根目录 `AGENTS.md`；
2. 阅读 `docs/implementation/project-baseline.md`；
3. 阅读当前改动涉及的 API、数据模型、状态机、Agent Runtime、Tool 和节点 Spec；
4. 从独立分支提交一个清晰的功能或修复。

## 本地环境

- Python 3.12
- Node.js 20
- PostgreSQL 16 + pgvector
- Docker Compose（推荐）

复制无密钥配置模板：

```powershell
Copy-Item .env.example .env
```

`.env.example` 默认启用 Mock Provider。不要把真实 Provider 调用加入普通测试或 CI。

## 架构约束

- Router 只负责 HTTP；Service 负责用例、事务和状态转换；Repository 负责持久化。
- Agent 节点不得直接操作 ORM Entity。
- 身份只来自 JWT Claims，不接受请求体中的 `user_id` 作为身份依据。
- SSE 事件必须先提交到 `agent_events`，再推送给客户端。
- 每个 Run 必须只有一个最终事件，并保留输入、配置和结果快照。
- LLM 结构化输出必须通过 Pydantic 校验，格式修复最多一次。
- 不引入 Redis、Celery、MCP、多 Agent、微服务或对象存储，除非先修订项目基线。

## 提交前验证

Windows：

```powershell
.\scripts\check.ps1
```

macOS/Linux：

```bash
./scripts/check.sh
```

完整检查包含 Ruff、Mypy、Alembic、Pytest、离线评测冒烟、Vitest 和前端生产构建。只修改文档时，至少检查 Markdown 本地链接、`git diff --check` 和敏感信息扫描。

## Pull Request 清单

- 说明用户问题、实现范围和明确非目标；
- 列出数据库迁移与 API 契约变化；
- 为 Schema、Service、Repository、API 和确定性 Agent 节点补充对应测试；
- 报告实际运行的命令和结果，不使用“理论上通过”；
- 不提交 `.env`、密钥、真实简历/JD、面试回答、数据库导出、模型权重或运行日志；
- 不把离线 Eval 指标描述为真实用户效果；
- 不顺手重构与当前任务无关的代码。

## Commit 建议

推荐使用简洁的 Conventional Commit 风格，例如：

```text
feat(interview): persist resumable answer turns
fix(runtime): fence stale lease writers
docs(readme): clarify mock startup and product limits
```
