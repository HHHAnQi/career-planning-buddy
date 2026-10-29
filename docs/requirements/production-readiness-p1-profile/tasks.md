# P1-5 用户画像读取边界：任务与验证

- [x] `PlanningContextPort` 增加 Profile 读取契约。
- [x] Executor 删除对 Graph Profile helper 的依赖。
- [x] Graph 不再直接读取 `UserProfile`。
- [x] `PlanningContextService` 复用现有 `ProfileRepository`。
- [x] 增加 Service 不直接查询 ORM 的架构守卫。
- [x] 聚焦 Ruff 与 Mypy 通过。
- [x] 聚焦 Pytest 由远程锁定依赖环境验证通过。
- [x] 远程 CI 通过。

## 影响

- 数据库迁移：无；
- API/OpenAPI：无变更；
- 运行时语义：不变；
- 新基础设施：无。

## 本地验证记录

- Ruff：通过；
- 定向 Mypy：通过；
- 全量 Mypy/Pytest：本机 `cp` 环境缺少锁定依赖 `pypdf`，
  未作为通过证据；
- 远程 CI：GitHub Actions run `40846484054` 的 Backend 与 Frontend 全部通过；
- 被验证提交：`83f6704b1b3fc6538f76bba676f78a0060c41d52`。
