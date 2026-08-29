# Release Evidence: v0.3-hardgate-88.9

当前验证发布的公开审计工件（脱敏）。由 `backend/scripts/build_release_evidence.py`
从实验数据库生成，可用同一实验 ID 复现。

| 文件 | 内容 |
|---|---|
| `experiment_manifest.json` | 实验元数据（数据集/模型/图版本/时间） |
| `frozen_config.json` | Run 冻结配置快照样例（预算/工具/记忆旋钮） |
| `case_manifest.json` | 30 case 清单 |
| `anonymized_trials.jsonl` | 90 trial：延迟/token/硬门禁/产出路径（run_id 盐哈希匿名） |
| `grades.jsonl` | 全量 grader 行（六域） |
| `summary.json` | 汇总 + 产出路径拆分 + 失败 trial 索引 |
| `failure_breakdown.json` | 失败 trial × grader 直方图 |
| `SHA256SUMS` | 全部工件内容哈希 |

## 头条数字（与 summary.json 一致）

- 硬门禁 **88.9%**（80/90，Wilson 95%CI [80.7, 93.9]）
- 延迟 P50 20.4s / P95 28.9s（nearest-rank 法）
- 产出路径拆分：model_pass 55 / deterministic_repair 11 / format_repair 1 /
  fallback 2 / no_plan_path（澄清/安全拒绝等非规划终态）21

## 复现

```bash
cd backend
python scripts/build_release_evidence.py cd3eb74e-0448-49a2-bc04-2758a9318990 \
  --name v0.3-hardgate-88.9
python scripts/confidence_report.py cd3eb74e-0448-49a2-bc04-2758a9318990
```

## 诚实边界

- 原始数据库为内部开发库；本包为**脱敏导出**，公开可复现性 = PARTIAL
  （summary 可追溯到 anonymized_trials 与 grades 逐行记录，但原始库不公开）
- 延迟分位为 nearest-rank，与 SLO 文档的 percentile_cont 相差 <0.3s
- 初始基线 72.2% 的同构证据包可按同法生成（早期实验）
