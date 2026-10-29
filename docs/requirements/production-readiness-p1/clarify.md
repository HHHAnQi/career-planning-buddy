# Production Readiness P1：Pairwise Calibration 边界收敛

> Status: verified

## 目标

让 Pairwise Calibration 报告生成恢复项目既定分层：Router 只处理 HTTP，Service 负责业务规则，Repository 负责查询。

## 当前问题

- Router 直接构造 SQLAlchemy `select`并执行查询；
- Router 负责 Sweep 一致性、Snapshot 构建和 Calibration 指标计算；
- 每个 Sweep 分别查询 Judge Result 和 Annotation，报告路径为 `2N` 次查询；
- 请求中的 dataset identity 未与 Sweep 持久化事实做一致性校验。

## 范围

- 报告生成的数据读取、校验、快照和指标计算迁入 Service；
- Repository 提供按多个 comparison group / sweep 的批量查询；
- 建立 API 层不直接执行 SQLAlchemy statement 的自动门禁；
- 保持成功响应 Schema 和幂等语义不变。

## 非目标

- 不拆分部署进程；
- 不修改数据库结构；
- 不更改 Calibration 统计口径；
- 不引入 Redis、Celery 或新的基础设施。
