# P1-3 规划上下文端口：实施计划

1. 在 `app.agent.ports` 定义 `PlanningContextPort` 和最小 Memory Query Facts。
2. 在 `app.services.planning_context` 实现原有 Plan、Review、Task、Interview 和 Memory 查询。
3. 通过 `AgentRunExecutor -> GraphFactory -> FixedPlanningGraph` 注入端口实现。
4. 删除 Graph 对上下文 Repository 和 Plan/Review ORM Model 的依赖。
5. 使用架构守卫和现有纵切测试证明行为等价。

## 验收

- Ruff 和 Mypy 通过；
- 架构测试禁止 Graph 重新引入上下文 Repository/ORM；
- Review/Replan、Memory Selection、Snapshot、fan-out 与全量 CI 通过；
- 无迁移、OpenAPI 和新运行时依赖。
