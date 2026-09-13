# 阶段交付定义

阶段与 `docs/implementation/` 一一对应。当前阶段未验收，不进入下一阶段。

## Stage 0：工程基线

交付：FastAPI/React 骨架、PostgreSQL、Alembic、测试、CI、Docker Compose。

验收：健康检查 200，后端测试和前端构建通过。

## Stage 1：契约、鉴权与画像

交付：核心枚举与 DTO、Guest JWT、users/user_profiles、画像 API、用户隔离测试。

验收：新设备可获取 token，创建和读取自己的画像，无法访问其他用户数据。

## Stage 2：Agent 纵切

交付：Mock Graph、agent_runs/agent_events、SSE、计划与任务持久化、取消和超时。

验收：无需真实模型即可完整完成“创建 Run → SSE → Plan/Tasks”。断线后可按 sequence 补发事件。

## Stage 3：执行反馈闭环

交付：真实 LLM Provider、任务状态机、每日复盘、重规划、结构化输出修复。

验收：用户从建档到复盘并生成新版计划；事务和幂等测试通过。

## Stage 4：记忆与证据

交付：记忆候选、用户确认、搜索来源、经验原子、Embedding/RAG、引用展示。

验收：检索结果有来源，敏感记忆必须经确认，删除/关闭记忆立即生效。

## Stage 5：评测与交付

交付：Trace 开发页、固定 Eval 数据集、回归报告、Docker 一键启动、Demo 资料。

验收：至少 30 条固定 Case 可重复运行，关键指标来自真实结果，README 有可复现 Demo。

## 统一完成条件

每个阶段都必须满足：

- 代码、迁移、测试齐全；
- 文档与实现一致；
- 无硬编码密钥和地区热线；
- 失败、超时、取消有明确终态；
- 验收命令由开发者实际执行并记录结果。
