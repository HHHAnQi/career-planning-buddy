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

**唯一报告口径 = 代码内容等价于 commit `97a3256`（实验 `cd3eb74e` 跑于该
提交的工作区，内容一致）+ 30 case × k=3 live（GLM-4.7，
BUSINESS_REPAIR_LLM_ENABLED=false 按下线判据）**：

| 指标 | 值 |
|---|---|
| trial 级硬门禁 | 80/90 = **88.9%** |
| case 级 pass_at_n（3/3 全过） | 25/30 = 83.3%（失败：repair-02/04 已知 mock-scripted 无效 case、live-mem-01 2/3（模型附加工具调用致精确匹配失败，见下）、replan-01/05 各 1/3） |
| 延迟 P50 / P95 | **20.2s / 28.6s**（修复节点 30s 独立上限 + LLM 修复下线后，P95 从 45.5–57.3s 降至 28.6s，SLO 由贴线转宽裕） |
| 单例离群 | replan-05 73.3s：同 case 姊妹 trial 20.5s 且 token 量几乎相同（2885/1039 vs 2898/991）→ 纯 provider 生成尾延迟（同量工作慢 3.5×），非重试/修复叠加（修复已禁用）。剔除 <60s 样本后 max 51.8s / P95 28.0s。已知尾行为，生产缓解方向：按 p99 感知的单调用重试（未实施，如实记录） |
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

- v1.6（2026-08-27）E1/C1/C2/E3 批次：① **E1 真裸模型基线**（`6b03e9af`，
  MEMORY_DISABLED+direct）：72.4%（21/29），live-mem 接地 0/3——推翻第一轮
  "裸模型 93.3%"的误读（该臂享图骨架），Agent 骨架贡献 ≈+21pp；② **E3 恢复
  实测**：graceful-shutdown 中断 + checkpoint 恢复，0 次新增物理 LLM 调用；
  ③ **C1 prompt v6**（记忆利用强化）：live-mem 3/3 硬门禁全过（含
  live-mem-01 工具匹配）；④ **C2 盲评**（独立 DeepSeek judge，n=8/臂）：
  记忆层对质量分无显著效应（D1 +0.12/D4 −0.37，噪声区间）——与
  memory_grounded 词面指标结论一致，质量维度诚实标记为"证据不足，
  prompt 迭代中"；⑤ memory_grounded grader 升级锚点评分 + 请求回声排除
  （词面信噪比限制已文档化）。E2（runaway 护栏专项数据集）未执行——
  护栏拦截已由 repair-03 61s 案例 + 30s 上限修复实证，专项数据集留待
  下一批次。

## 统计效力审计（2026-08-27，第二轮拷问 Q3 闭环）

`scripts/confidence_report.py`（Wilson 95% CI + 两比例 z 检验）对全部头条
数字补置信区间：

| 结论 | 点估计 | 显著性 |
|---|---|---|
| Agent 骨架贡献（真裸 72.4% vs 全量 93.3%，k=1） | +20.9pp | **p=0.032 ✅ 显著** |
| 记忆层贡献（OFF 80.0% vs ON 93.3%，k=1） | +13.3pp | **p=0.129 ❌ k=1 不显著（CI 跨 0，主张降级待 k=3 确认）** |
| 权威口径 k=3 | 88.9% [80.7, 93.9] | — |

制度性教训已入 `docs/standards/metric-registry.md`：无 CI 的数字不构成主张。

## 记忆层成本账本（第二轮拷问 Q4，当前 30-case 世界可观测账）

| 项 | 实测值 |
|---|---|
| 注入成本（工具路径） | 仅 live-mem 3/30 case 走 memory_lookup，结果负载 130 字符/run（≈65 token） |
| 注入成本（pinned 通道） | 本实验 0 行（planted 记忆经工具通道流入） |
| embedding 成本 | 226ms 均值/983ms 峰值——已被 fan-out 并行吸收 |
| 记忆工具延迟 | 200–483ms，异步于主生成 |
| 净 token 效应 | ON 臂输入均值反而低 9.1%（接地减少修复轮） |
| 删除的可观测退化 | live-mem 0/3、replan 上下文丢失；硬门禁差待 k=3 CI 确认 |

- v1.7（2026-08-27）第二轮拷问修复批次：① 臂配置断言
  （`evals/v2/arm_invariants.py`：pre-trial config 校验 + 评分时轨迹校验，
  6 项故障注入测试，"假裸臂"类错误机器层面无法存活）；② 统计效力审计
  （`scripts/confidence_report.py`：Wilson CI + 两比例检验；记忆层 +13.3pp
  在 k=1 不显著已如实降级，k=3 两臂补跑进行中）；③ 并发正确性三证明
  （`tests/test_run_exclusive_concurrency.py`：锁等待不耗节点超时、
  pool_size=1 免锁安全、4-run 并发压测全过）；④ 指标预注册制度
  （`docs/standards/metric-registry.md`，memory_grounded 演进作为反面教材
  永久保留）；⑤ 记忆层成本账本（注入 ≈65 token × 3 case，embedding 已被
  并行吸收，净 token 为负）；⑥ rubric v10 D3/D4 歧义消解判例 5 条 +
  worksheet 生成（23 行待人工标注）。

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
