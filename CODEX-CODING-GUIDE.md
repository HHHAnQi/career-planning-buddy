# 使用 Codex 分阶段实现代码

> Codex 是本项目的编码与工程执行助手，不是项目底座，也不是运行时模型。本项目独立开发，不引用 ClawAgent。

## 推荐工作方式

从仓库根目录启动 Codex，使根目录 `AGENTS.md` 生效。每次只执行一个 Stage 或一个清晰的纵切任务；先创建新 Git 分支，完成后检查 diff、运行验收命令，再提交。

Codex 可以直接读取、修改文件并运行命令，因此任务描述应同时给出：目标、非目标、权威文档、验收命令和禁止越界项。不要只说“按文档把项目写完”。

## 通用提示词模板

```text
你现在负责 Career Planning Buddy 的 Stage {N}。

必须先阅读：
1. AGENTS.zh-CN.md
2. docs/implementation/project-baseline.md
3. docs/implementation/stage-{N}-*.md
4. 当前任务涉及的 API、data-model、state-machine、agent-runtime、tool、agent-node spec

项目是独立的新项目，不以 ClawAgent 为底座，不允许导入或假设任何 ClawAgent 模块。

请先只输出：
- 你理解的本阶段目标与非目标
- 计划新增/修改的文件清单
- 数据库迁移与 API 变化
- 测试计划
- 发现的文档冲突或待确认问题

等我确认后再生成代码。实现时必须遵守分层、状态机、用户隔离、幂等、SSE 事件先落库后推送等约束。完成后运行任务书中的验收命令，逐条报告真实结果；不要自动进入下一阶段。
```

## Stage 对应文件

| Stage | 任务书 |
|---|---|
| 0 | `docs/implementation/stage-0-foundation.md` |
| 1 | `docs/implementation/stage-1-contracts-profile.md` |
| 2 | `docs/implementation/stage-2-agent-run.md` |
| 3 | `docs/implementation/stage-3-review-replan.md` |
| 4 | `docs/implementation/stage-4-memory-rag.md` |
| 5 | `docs/implementation/stage-5-eval-delivery.md` |

## 每轮人工检查

1. `git diff --stat`：是否超出本阶段范围；
2. 查看新增依赖：是否擅自引入 Redis、Celery、MCP、多 Agent或微服务；
3. 查看迁移：字段、索引、唯一约束是否与 data-model spec 一致；
4. 查看 Router：是否把业务逻辑塞进接口层；
5. 查看 Service：状态机和事务是否完整；
6. 查看 Agent：是否遵守 Runtime 预算、快照、终态唯一，是否直接操作 ORM；
7. 查看 Tool：是否只读、白名单、Schema、超时、用户隔离与 fixture；
8. 查看测试：是否真的覆盖越权、冲突、超时和失败；
9. 实际运行验收命令，不接受“理论上可通过”。

## 失败时的处理

不要让 Codex 整体重写。把失败命令、完整错误栈、相关文件和预期行为交给它，只要求修复当前错误，并重新运行对应测试。
