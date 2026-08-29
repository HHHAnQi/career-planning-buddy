# CLAIM_EVIDENCE_MATRIX.md（2026-08-29）

> 规则：Verdict ∈ VERIFIED / SUPPORTED_WITH_LIMITATION / INTERNAL_ONLY / REMOVE。
> 全仓数字与性能 claim 的证据映射；简历/README 只允许引用 VERIFIED 与
> SUPPORTED_WITH_LIMITATION（后者须带限定语）。

| Claim | Evidence | Reproducible | Public Artifact | Verdict |
|---|---|---|---|---|
| 初始 live 基线硬门禁 72.2%（k=3） | 2026-08-26 早期实验 | DB 可复算 | 待发布证据包 | VERIFIED（须标注 "initial baseline, 早期 commit"） |
| 当前验证结果 88.9%（k=3，Wilson [80.7, 93.9]） | 实验 `cd3eb74e`，代码 `97a3256`，2026-08-26 | `scripts/confidence_report.py` | 待发布证据包 | VERIFIED（标注 commit/日期） |
| P95 28.6s / P50 20.2s（同上实验） | 同上 | 同上 | 同上 | VERIFIED |
| Agent 骨架贡献 +20.9pp（真裸 72.4% vs 全量 93.3%，p=0.032） | 实验 `6b03e9af` vs `85d6ba48`（k=1） | confidence_report | 内部 | SUPPORTED_WITH_LIMITATION（k=1 双臂，n=29/30） |
| 记忆层 +13.3pp | k=1 单次；k=3 复跑缩至 +4.3pp（p=0.429） | confidence_report | — | **REMOVE（已按预注册制度撤回）** |
| 记忆层价值 = 接地能力（live-mem 0/3 vs 3/3）+ 零边际成本 | 消融 `7646a9e8`/`85d6ba48` + 成本账本 | 可复算 | 内部 | SUPPORTED_WITH_LIMITATION（n=3 case） |
| 恢复 0 次新增物理 LLM 调用（checkpoint 复用） | `crash_recovery_measurement.py` E3（n=1 演示） | 脚本可重跑 | — | SUPPORTED_WITH_LIMITATION（单次演示，机制有单测） |
| 检索 Recall@5=1.0 / MRR 0.933 | retrieval-v2 硬化集（20 case 含负例） | `run_retrieval_eval` | — | VERIFIED（须带数据集规模限定） |
| 评测κ=0.679 | D1 维度双标注 | calibration 脚本 | — | SUPPORTED_WITH_LIMITATION（仅 D1；D3/D4=0.40 须并述） |
| 814 项测试全绿 / ruff 0 / mypy(app) 0 | 本地 CI 等价（2026-08-29） | 本地可重跑 | Actions 待 push 后公开 | VERIFIED（标注 "local verification; Actions pending push"） |
| "99.9% availability" | 不存在任何测量窗口/SLI/停机定义 | — | — | **REMOVE（全仓未发现，禁止未来引入）** |
| "1.5s latency" 类表述 | 全仓未发现；真实 Run P50 20.2s | — | — | N/A（如出现按 E2E 口径纠正） |
| mock CI 100% | CI 步骤 stage5 mock eval + 814 测试 | Actions | — | VERIFIED |
| 单 worker / 无 HA 验证 | compose 单副本；多进程测试未做 | — | — | 保留为 LIMITATION（不得写成 HA） |

## 简历最终推荐表述（仅用上表 VERIFIED/SUPPORTED 项）

- 硬门禁 72.2%→88.9%（k=3，带 CI）；P95 45.5s→28.6s
- 混合检索 Recall@5=1.0（20 case 硬化集）
- 归因/消融/预注册制度（"记忆层 +13pp" 不得出现在任何材料）
