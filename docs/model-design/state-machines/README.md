# 状态机索引

状态机是 Service 层的强约束，Agent 只能提出候选结果，不能绕过状态转移。

- [Run 状态](./run-status.mmd)
- [Plan 状态](./plan-status.mmd)
- [Task 状态](./task-state.mmd)
- [意图路由](./intent-routing-flow.mmd)

所有枚举以 [API 与数据契约](../../architecture/api-and-data-contracts.md) 为准。Run 终态还必须满足 result_kind 和唯一 terminal event 约束。
