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

## 终版权威口径（2026-08-26，实验 `cd3eb74e`）

**唯一报告口径 = 当前 HEAD（提交见 git log）+ 30 case × k=3 live（GLM-4.7，
BUSINESS_REPAIR_LLM_ENABLED=false 按下线判据）**：

| 指标 | 值 |
|---|---|
| trial 级硬门禁 | 80/90 = **88.9%** |
| case 级 pass_at_n（3/3 全过） | 25/30 = 83.3%（失败：repair-02/04 已知 mock-scripted 无效 case、live-mem-01 2/3（模型附加工具调用致精确匹配失败，见下）、replan-01/05 各 1/3） |
| 延迟 P50 / P95 | **20.2s / 28.6s**（修复节点 30s 独立上限 + LLM 修复下线后，P95 从 45.5–57.3s 降至 28.6s，SLO 由贴线转宽裕） |
| 单例离群 | replan-05 73.3s（单次生成慢，非修复环叠加——修复调用已禁用） |
| fan-out 实测收益 | embedding 均值 226ms / 峰值 983ms 与证据分支重叠（memory step DB 段仅 7ms） |
| memory_grounded（新质量 grader，非硬门禁） | **3/9 = 33%**——诚实短板：记忆注入了上下文但模型未充分落进计划文本，下一迭代目标（prompt 强化 pinned_memories 利用） |

历史数字（92.2% k=3 旧代码 / 83.3% k=1 / 93.3% k=1 重构后）仅作过程记录，
现状以本表为准。

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

- v1.4（2026-08-26）二次整改批次：
  ① **延迟回归记录**：k=1 归因实验中 repair-03 单例 61.0s 破 60s 线
  （成因：修复环二次 LLM 调用叠加，5130 入 / 2043 出 token）。处置：该类
  case 在修复预算后仍失败时应直接降级（当前行为已如此），后续迭代把
  `revise_or_fallback` 节点超时从 deadline 共享改为独立上限，防止贴线恶化。
  ② **图拓扑升级**：context_builder 拆为 `memory_loader ∥ evidence_loader`
  真·LangGraph fan-out 并行节点 + context_builder join（`run_exclusive`
  单锁段串行化单连接运行时；embedding 网络调用在锁外保持真实重叠），
  并行断言测试 `tests/test_parallel_context_fanout.py`。
  ③ **DirectLLM 基线对照**（实验 `8d3f4781`，direct_llm_v1 臂）：裸模型
  硬门禁 93.3% vs Agent 臂 83.3%（k=1，同 30 case）。裸模型结构化输出
  能力强是前提而非否定——Agent 增量在记忆接地/工具/预算/可恢复性，且
  对照暴露了修复环降级模板的质量短板（repair-03/replan-05 裸模型直出
  通过而 Agent 降级失败），列为下一迭代目标。

- v1.5（2026-08-26）第三轮修复批次：
  ① `BUSINESS_REPAIR_LLM_ENABLED` 旋钮 + LLM 修复下线判据（滚动 100 次
  rescue rate < 5% → 关闭；当前 0/6 已触发，归因报告自动输出建议）；
  ② `revise_or_fallback` 节点独立 30s 超时上限（repair-03 61s 破线根因）；
  ③ 上下文相关性打分升级为 bigram+embedding 混合（同义词回归测试背书）；
  ④ fan-out 锁改为运行时判据（Connection 绑定加锁 / Engine 池免锁）+
  `CONTEXT_FANOUT_ENABLED` 串行 A/B 模式 + `embed_latency_ms` 埋点；
  ⑤ 新增 `model.memory_grounded` 质量 grader（非硬门禁：计划文本须命中
  planted memory ≥ 半数，bigram 命中率 ≥ 0.10）——补齐记忆层对"规划质量"
  贡献的度量缺口；⑥ 新增 `docs/architecture/langgraph-runtime-notes.md`
  （五个机制点 + 踩坑实证）。
