# 可重复本地验证：任务与验收

- [x] macOS/Linux 门禁要求项目虚拟环境或显式 Python 覆盖。
- [x] Windows 门禁要求项目虚拟环境或显式 Python 覆盖。
- [x] 两端都校验 Python 3.12。
- [x] Mypy 范围与 CI 收口为 `app`。
- [x] 默认门禁数据库改为 `career_buddy_test`。
- [x] 本地安装说明使用锁定依赖。
- [x] Shell 语法、缺失环境和错误 Python 版本分支验证通过。
- [x] PowerShell 脚本与 Shell 保持等价的环境、版本、Mypy 和数据库契约。
- [ ] 远程 CI 通过。

## 本地验证限制

当前宿主只提供 Python 3.13，且未安装 PowerShell，因此不将它作为
Python 3.12 全量门禁或 PowerShell 运行的通过证据。完整工程验证由锁定依赖的
GitHub Actions 执行。
