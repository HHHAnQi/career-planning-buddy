# P1-5 用户画像读取边界：任务与验证

- [x] `PlanningContextPort` 增加 Profile 读取契约。
- [x] Executor 删除对 Graph Profile helper 的依赖。
- [x] Graph 不再直接读取 `UserProfile`。
- [x] `PlanningContextService` 复用现有 `ProfileRepository`。
- [x] 增加 Service 不直接查询 ORM 的架构守卫。
- [x] 聚焦 Ruff 与 Mypy 通过。
- [ ] 聚焦 Pytest 通过。
- [ ] 远程 CI 通过。

## 影响

- 数据库迁移：无；
- API/OpenAPI：无变更；
- 运行时语义：不变；
- 新基础设施：无。

## 本地验证记录

- Ruff：通过；
- 定向 Mypy：通过；
- 全量 Mypy/Pytest：本机 `cp` 环境缺少锁定依赖 `pypdf`，
  尚未作为通过证据；由远程 CI 的锁定依赖环境继续验证。
