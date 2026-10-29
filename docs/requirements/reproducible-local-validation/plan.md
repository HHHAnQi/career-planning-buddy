# 可重复本地验证：实施计划

1. 本地门禁默认强制使用 `backend/.venv`。
2. 保留显式 `BACKEND_PYTHON` 覆盖，供 CI 或受控环境使用。
3. 执行前校验 Python 3.12，缺失环境时返回可操作的错误。
4. 将本地 Mypy 范围收口为 CI 已强制的 `app`。
5. 将默认数据库改为 `career_buddy_test`。
6. 更新本地开发文档，使用 `requirements.lock` 和 `npm ci`。

无数据库迁移，无 API 变更。
