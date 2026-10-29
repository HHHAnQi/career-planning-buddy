# Eval 中断恢复边界：实施计划

1. Repository 提供带行锁的 running Experiment 查询。
2. Repository 批量收敛 pending/running Trial 的中断状态。
3. Service 在单一事务中执行恢复用例。
4. Executor 只管理进程内 Task 和启动时调用 Service。
5. 增加架构守卫与 Service/Executor 回归测试。

无数据库迁移，无 API 变更。
