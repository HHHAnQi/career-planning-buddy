# P1-5 用户画像读取边界：实施计划

1. 扩展 `PlanningContextPort`，提供 `load_profile`。
2. 由 `PlanningContextService` 实现该契约。
3. 复用 `ProfileRepository`，不在 Service 中直接查询 ORM。
4. Executor 通过同一 Context Service 读取 Profile 并注入 Graph。
5. 增加架构边界测试和聚焦回归。

无数据库迁移，无 API 变更。
