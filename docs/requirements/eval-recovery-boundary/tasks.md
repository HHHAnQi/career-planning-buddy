# Eval 中断恢复边界：任务与验收

- [x] Executor 删除 SQLAlchemy、ORM Model 和 Repository 恢复依赖。
- [x] Service 承担 Eval 中断恢复用例。
- [x] Repository 承担带锁查询和 Trial 批量更新。
- [x] 保留 pending → cancelled、running → failed 语义。
- [x] 保留 `PROCESS_INTERRUPTED` 失败分类。
- [x] 增加 Executor 持久化边界守卫。
- [x] 定向 Ruff 与差异检查通过。
- [ ] Python 3.12 Mypy 和聚焦 Pytest 通过。
- [ ] 远程 CI 通过。

## 本地验证限制

当前宿主只有 Python 3.13，定向 Mypy 会同时加载无关模块并命中现有的
`nodes.py` 与 Settings 插件差异；本地环境也缺少 `pypdf`。因此完整类型与
数据库回归以 Python 3.12 锁定依赖 CI 为验收证据。
