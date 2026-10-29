# Eval 中断恢复边界：范围确认

## 决策

普通 Eval Experiment 保持单 Worker 运行边界。进程中断后不自动重试 Trial，
而是将 Experiment 收敛为 `failed`，pending Trial 收敛为 `cancelled`，
running Trial 收敛为 `failed`，并保留 `PROCESS_INTERRUPTED` 证据。

## 理由

Eval Trial 可能已创建 Agent Run、Plan、Event 并产生 Provider 成本。在没有
Trial 级副作用幂等和检查点前自动续跑，会隐藏重复执行和评测污染。
显式创建新 Experiment 比伪造 exactly-once 更容易审计。

## 本切片不做

- 不增加 lease/heartbeat 字段或数据库迁移；
- 不引入 Redis、Celery 或通用 Job 平台；
- 不改变 API、Experiment/Trial 状态机和失败分类；
- 不改造 Pairwise Sweep。
