# AI 编码协作规则

本文件是 Codex 的中文项目规则补充。根目录 `AGENTS.md` 是 Codex 的首要入口，本文件提供更详细的中文约束。

## 1. 项目事实

- 本项目是独立的新项目，不以 ClawAgent 为底座。
- 禁止导入、复制或假设存在任何 ClawAgent 模块。
- 当前代码必须以本仓库文档定义的接口、数据表和状态机为准。
- 编码助手只是开发工具；运行时模型由环境变量配置。

## 2. 必读顺序

开始任何代码任务前依次阅读：

1. `docs/implementation/project-baseline.md`
2. 当前阶段任务书 `docs/implementation/stage-*.md`
3. 相关 `docs/model-design/api-spec/*.md`
4. 相关 `docs/model-design/data-models/*.md`
5. 相关 Agent Runtime、Tool、状态机和节点 spec

不要先扫描全部 100 多份文档后自行拼接结论。

## 3. 实现规则

- 一次只做一个阶段或一个纵切用例。
- 开始编码前先输出：目标、涉及文件、数据库变更、接口变更、测试计划。
- 不得擅自增加 Redis、Celery、MCP、多 Agent、对象存储或微服务。
- Router 只处理 HTTP；业务规则放 Service；数据库访问放 Repository。
- Agent 节点不得直接写 ORM，所有业务写入必须经 Service。
- Stage 2~4 必须遵守 `agent-runtime` 的预算、快照、取消、终态唯一和结果类型契约。
- Tool 只读、显式白名单、参数/结果 Schema、超时、用户隔离和 Replay fixture 缺一不可。
- 所有外部模型调用只通过 Provider Protocol。
- API Request 不接受 `user_id`，用户身份只从 JWT 获取。
- 写接口必须处理幂等或乐观锁。
- 所有状态变化必须符合状态机。
- SSE 事件必须先持久化到 `agent_events`，再推送给前端。
- Prompt 输出必须用 Pydantic Schema 校验；解析失败最多修复一次。

## 4. 技术约束

- Python 3.12；FastAPI；Pydantic v2；SQLAlchemy 2 Async。
- PostgreSQL 16；Alembic；MVP 单 Worker。
- 后端不得使用同步数据库驱动。
- 前端使用 TypeScript strict，不使用 `any` 绕过契约。
- 时间统一存 UTC，API 返回 ISO 8601。
- 金额使用 Decimal 或整数最小单位，不使用浮点累计真实账单。
- UUID 示例必须是合法 UUID，不使用 `u-xxx` 之类伪 UUID。

## 5. 测试要求

每个任务至少包含：

- Schema 校验测试；
- Service 状态机测试；
- Repository 集成测试；
- API happy path 与主要错误路径；
- Agent 节点使用 Mock Provider 的确定性测试。

不得用“代码看起来正确”代替执行测试。

## 6. 输出格式

完成任务时给出：

1. 修改文件列表；
2. 关键设计说明；
3. 运行过的命令及结果；
4. 尚未完成或无法验证的内容；
5. 下一阶段建议，但不要自动跨阶段继续生成。
