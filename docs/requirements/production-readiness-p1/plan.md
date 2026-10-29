# 实施计划

> Status: implementing

## 调用链

```text
POST /eval/pairwise/calibration
  → PairwiseCalibrationService.generate_calibration_report
    → EvalRepository.get_sweep
    → EvalRepository.list_judge_results_by_comparison_groups
    → EvalRepository.list_annotations_by_sweeps
    → compute_calibration_status_from_snapshots
    → create_or_reuse_calibration_report
```

## 业务规则

1. 所有 Sweep 必须存在；
2. 所有 Sweep 必须属于请求声明的 dataset id/version；
3. 所有 Sweep 必须共享 Judge model/prompt/rubric identity；
4. Snapshot 按批量查询结果构建；
5. 报告的 input/content hash 和现有幂等矩阵不变。

## 事务边界

读取和指标计算不持有长事务；报告查重与写入继续由
`create_or_reuse_calibration_report` 的现有事务边界保护。

## 兼容性和回滚

- 无迁移；
- 无新增成功响应字段；
- 新增 `EVAL_CALIBRATION_DATASET_MISMATCH` 409 用于拒绝原先可能写入错误归属的报告；
- 可通过回滚单一提交恢复原路径。
