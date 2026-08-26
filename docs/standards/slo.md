# 服务等级目标（SLO）v1 —— 2026-08-26

> 评测的输出不是裸数字，是"达标/不达标"。本文件是唯一权威的目标定义；
> `scripts/slo_report.py` 按此生成达标报告（任一未达 → 退出码非零）。
> 目标值变更必须有评测数据支撑，走 PR 并在此记录修订历史。

## 目标定义

| ID | 指标 | 目标 | 口径 | 现状（2026-08-26） | 判定 |
|---|---|---|---|---|---|
| `latency-planning-p95` | 规划 Run P95 延迟 | ≤ 60s | 真实 GLM 终态 Run，近 7 天 | 57.3s（thinking disabled 后） | ⚠️ 贴线 |
| `quality-live-hard-gate` | 真实硬门禁通过率 | ≥ 85% | 最新 live 实验（k=3） | **92.2%**（+20pp，全部三项修复驱动） | ✅ 达标 |
| `cost-planning-run` | 单次规划成本 | ≤ ¥0.01 | 真实 Run 平均（牌价估算） | ¥0.0037 | ✅ |
| `retrieval-hybrid-recall` | 混合检索 Recall@5（硬化集） | ≥ 0.90 | retrieval-v2，bge-m3 | 1.00 | ✅ |
| `regression-mock-gate` | mock 回归门禁 | 100% | CI（stage5+intent，每 PR） | 100% | ✅ |

## 设计说明

- hard gate 已达标（92.2% ≥ 85%），红着的剩余项是 latency P95（57.3s vs 60s 贴线）。
- **口径绑定数据源**：延迟/成本来自 `agent_runs`（真实模型），质量来自最新
  live 实验，检索来自 v2 硬化集，回归来自 CI——报告脚本按此取数，不接受
  手工填报。
- **修订历史**：v1（2026-08-26）首次定义。

## 使用

```bash
cd backend
python -m scripts.slo_report                      # 全量（DB 指标 + 检索）
python -m scripts.slo_report --db-only            # 仅 DB 可得指标
# 检索指标需先跑: python -m scripts.run_retrieval_eval --dataset retrieval-v2
# 双维度归因（模型 vs 工程贡献拆分）: python scripts/attribution_report.py
```

任一 SLO 未达 → 退出码 1（可接 CI / 部署前检查）。

### live 实验 runbook（重要）

**跑 in-process live 实验前必须 `docker stop career-planning-buddy-backend-1`。**
dev 容器的 intake worker 会在共享库里抢新建的 pending Run（lease 先到先得），
导致 trial 以 `RUN_NOT_COMPLETED` 失败（2026-08-26 实测：14/30 trial 被抢）。
实验结束后 `docker start` 恢复。pytest 已用独立库 `career_buddy_test` 隔离，不受影响。

## 修订历史

- v1（2026-08-26）首次定义。
- v1.1（2026-08-26）确定性修复后更新：hard gate 72.2% → 82.2%（+12pp，
  驱动因素：replan 确定性修复 + 记忆预执行 + GLM 禁推理）。85% 目标不变。

- v1.2（2026-08-26）最终结果：hard gate 92.2%（+20pp from 72.2% baseline）✅ 达标。
  27/30 case 3/3 全过；仅剩 repair-02/04 全败（mock-scripted，live 无效 case）。

- v1.3（2026-08-26）新增双维度归因体系：图 persist 节点写 `run.provenance`
  事件（model_pass / format_repair / deterministic_repair / llm_repair / fallback
  五互斥标签），`scripts/attribution_report.py` 对最新 live 实验输出模型能力
  占比 vs 工程兜底占比 + 未知规则 backlog。同时新增未知违规观测
  （`agent_unknown_rule_total` 计数器）与三级上下文压缩（相关性召回→动态
  预算→摘要折叠）。
