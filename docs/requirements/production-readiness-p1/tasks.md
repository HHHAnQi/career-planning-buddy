# 任务与验收

> Status: implementing

- [x] Repository 新增 Judge Result 批量查询；
- [x] Repository 新增 Annotation 批量查询；
- [x] Sweep、dataset 和 Judge identity 校验迁入 Service；
- [x] Snapshot 和 Calibration 指标计算迁入 Service；
- [x] Router 删除 SQLAlchemy 查询和业务计算；
- [x] 新增 API/Repository 边界门禁；
- [x] 新增 dataset mismatch Service 测试；
- [ ] Ruff、Mypy、Pytest 和 Eval 在 Python 3.12 CI 中通过；
- [ ] 远程 CI 通过后将状态改为 `verified`。

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
