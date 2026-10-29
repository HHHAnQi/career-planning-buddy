# P1-2 Agent Graph 纯逻辑边界：实施计划

1. 新建 `app.agent.runtime_support`，承接输入指纹、checkpoint 编解码、usage 合并与遥测组装。
2. `FixedPlanningGraph` 仅调用这些函数，保留 checkpoint 读写、日志与状态更新编排。
3. 为纯函数增加边界用例，并使用现有 checkpoint 集成测试证明行为等价。
4. 增加 AST 架构守卫，禁止纯逻辑模块依赖 Graph、数据库、Model、Provider 和 Repository。

## 验收条件

- 新模块 Ruff 通过；
- 纯逻辑、checkpoint 恢复、Agent 节点、并行上下文和故障注入测试通过；
- 全量后端测试和 CI 通过；
- 无迁移、OpenAPI 变更和新运行时依赖。
