# 任务与验收

> Status: verified

- [x] Repository 新增 Judge Result 批量查询；
- [x] Repository 新增 Annotation 批量查询；
- [x] Sweep、dataset 和 Judge identity 校验迁入 Service；
- [x] Snapshot 和 Calibration 指标计算迁入 Service；
- [x] Router 删除 SQLAlchemy 查询和业务计算；
- [x] 新增 API/Repository 边界门禁；
- [x] 新增 dataset mismatch Service 测试；
- [x] Ruff、Mypy、820 项 Pytest 和 Eval 在 Python 3.12 CI 中通过；
- [x] 远程 CI 通过，任务状态改为 `verified`。

## 验证记录

- Commit: `db65d463b75f178450280f408fe8568aa826f1c9`；
- GitHub Actions: `40828909493`；
- Backend: Ruff、Mypy、配置审计、公开声明审计、Alembic 往返迁移、
  Compose 校验、非 root 镜像、820 项 Pytest、两组离线 Eval 全部通过；
- Frontend: Vitest 与生产构建通过；
- 本地聚焦验证：55 项 Pairwise/Boundary/Public Claims 测试通过；
- 迁移影响：无；API 成功响应和 OpenAPI Snapshot 不变。

## 验收命令

```bash
cd backend
python -m ruff check app/api/pairwise_calibration.py \
  app/services/pairwise_calibration.py app/repositories/evals.py \
  tests/test_architecture_boundaries.py \
  tests/evals_v2/test_pairwise_calibration_service.py \
  tests/evals_v2/test_pairwise_calibration_repository.py
python -m mypy app
python -m pytest tests/test_architecture_boundaries.py \
  tests/evals_v2/test_pairwise_calibration_service.py \
  tests/evals_v2/test_pairwise_calibration_repository.py \
  tests/evals_v2/test_pairwise_calibration_api.py
```
