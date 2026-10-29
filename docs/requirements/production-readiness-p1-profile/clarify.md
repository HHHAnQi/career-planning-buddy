# P1-5 用户画像读取边界：范围确认

## 目标

将 Career Planning Run 的用户画像读取收口到现有
`PlanningContextPort`，使 Executor 和 Graph 不直接读取 ORM 实体。

## 本切片不做

- 不改变 Profile API 或数据表；
- 不新增通用容器或依赖注入框架；
- 不重构其他 Profile 用例；
- 不引入新的后台任务基础设施。

## 不变量

- Profile 缺失时仍返回 `None`；
- Profile 转换仍使用 `ProfileContext`；
- 持久化查询复用现有 `ProfileRepository`；
- 不改变 Run 状态机、API 契约或迁移。
