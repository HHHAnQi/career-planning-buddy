# 分阶段实现任务书

这组文档用于让 Codex 按阶段实现代码。一次只执行一份任务书，不要整仓一次生成。

| 阶段 | 任务书 | 核心产物 |
|---|---|---|
| 0 | [工程基线](./stage-0-foundation.md) | FastAPI、React、PostgreSQL、Alembic、CI |
| 1 | [契约与画像](./stage-1-contracts-profile.md) | Schema、表迁移、Guest JWT、Profile |
| 2 | [Agent 纵切](./stage-2-agent-run.md) | Mock Graph、Run、Event、SSE、Plan、Task |
| 3 | [执行反馈闭环](./stage-3-review-replan.md) | 真实 LLM、任务状态、复盘、重规划 |
| 4 | [记忆与证据](./stage-4-memory-rag.md) | Memory、Search、Embedding、RAG |
| 5 | [评测与交付](./stage-5-eval-delivery.md) | Trace、Eval、Docker、Demo |

## 执行纪律

1. 当前阶段验收命令未通过，不进入下一阶段。
2. 每次任务先生成实现计划，再生成代码。
3. 不允许为了“先跑起来”绕过状态机、用户隔离或数据库迁移。
4. 真实模型接入前，Mock Provider 的完整纵切必须通过。
5. 文档与代码冲突时，以 [project-baseline.md](./project-baseline.md) 为最高事实源。
