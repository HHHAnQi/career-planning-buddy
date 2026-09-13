# 测试与 TDD

## 1. 测试层次

| 层 | 内容 |
|---|---|
| Unit | 状态机、规则校验、Schema、预算、Prompt 组装 |
| Integration | PostgreSQL Repository、事务、唯一约束、Alembic |
| API Contract | 状态码、错误码、用户隔离、OpenAPI、SSE |
| Agent Vertical | Mock Provider 下完整 Run |
| Eval | 计划质量、安全分流、RAG 来源和回归 |
| Smoke | Docker Compose 完整用户旅程 |

## 2. 必测场景

- 用户 A 不能访问用户 B 数据；
- 重复 Idempotency-Key 返回同一结果；
- 同用户不能并发创建两个活动 Run；
- Task/Plan/Run 非法状态转移返回 409；
- SSE sequence 单调、断线可补发、终态后关闭；
- LLM 超时、格式错、Tool 失败、取消、deadline 均收敛；
- Review 的完成/放弃计数来自数据库任务事实；
- 重规划成功前不归档旧计划。

## 3. Mock 与真实 Provider

CI 默认使用确定性 Mock，不调用付费网络。真实 Provider 测试放在显式 marker 或手工流水线中，记录模型、Prompt 版本、成本和时间，失败不能被伪装成单元测试通过。

## 4. Eval

Stage 5 至少准备 30 条 JSONL Case，覆盖建档不足、计划生成、任务时间约束、复盘重规划、检索来源和风险分流。指标至少包括：结构化成功率、任务约束满足率、可启动性、可验证性、来源覆盖率、安全分流准确率、平均耗时和成本。

目标阈值是项目目标，不得在实际运行前写成已达到。
