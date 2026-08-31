# 上下文管理效果验证 · 离线批次报告（context-compression-v1）

> 2026-08-31 ｜ 全部结果为**合成/离线**（零模型调用）｜ 指标口径预注册于
> `docs/standards/metric-registry.md`（跑前冻结）｜ 真实模型效果**待验证**

## 1. 已有能力 / 缺口 / 修改文件（步 1 盘点结论）

| 项 | 原状 | 本批处理后 |
|---|---|---|
| 压缩策略 | 单一（recent + 相关性 + 摘要混在一起，不可切换对照） | 三策略可切换：`full` / `recent` / `relevant_summary`（`CompressionStrategy`） |
| 预算对齐 | — | recent 与 relevant_summary **共用同一预算**（tasks=5/reviews=2），对照只差相关性+摘要机制 |
| 裁剪溯源 | 只有计数 | 每条被裁记录带 `reason`（recency_window / budget_shrink / irrelevance）；摘要行 → 原始 deliverable 映射（`summary_sources`） |
| 超预算状态 | 收缩到地板后**静默继续** | `over_budget` 显式标志（保留底线后仍超预算时置位，不删约束） |
| **校验独立性（关键缺口）** | `validate_candidate` 读**压缩后** completed_facts/recent_tasks —— 压缩可削弱业务校验 | 图新增 `authoritative_context`（未压缩），validator 与确定性修复 recheck 全部改读权威事实 |
| 输入长度记录 | 仅 provider 实际用量 | 最终渲染请求的分段估算入 trace：`input_estimate_{system,user,tools,total}_tokens`（**估算口径**，与 provider `tokens_in` 实际口径区分） |
| 配置冻结 | — | `CONTEXT_COMPRESSION_STRATEGY` 旋钮进 settings/snapshot/env/compose，进 config snapshot 可审计 |

修改文件：`app/agent/context_compression.py`、`app/agent/graph.py`、`app/schemas/agent_runs.py`、`app/core/config.py`、`app/harness/snapshots.py`、`app/prompts/career_planning.py`、`.env.example`、`compose.yaml` + 新增数据集/运行器/测试/真实脚本。

## 2. 离线对照结果（合成数据，8 case × 3 策略，零模型调用）

| 策略 | 输入降幅均值* | 必需事实保留均值 | over-budget case |
|---|---|---|---|
| full（基线） | 0%（定义使然） | 1.00 | 1/8（cc-budget-07，400 token 极限预算） |
| recent（纯近因窗） | **9.0%** | **0.771**（丢 22.9% 必需事实） | 0/8 |
| relevant_summary（相关性+摘要） | **8.1%** | **1.00** | 1/8（显式状态，未静默删约束） |

*估算口径：保守 CJK/Latin 估算器作用于渲染后上下文段；**非精确 tokenizer、非 provider 实际用量**。

**离线结论（仅对合成分布成立）**：纯近因窗会系统性丢失"较早但相关"的必需事实
（old-but-relevant / review-heavy 场景）；相关性+摘要机制以约 1pp 的降幅代价
换回全部必需事实；极限预算下 over_budget 显式暴露而非静默。**不预设任何
真实流量下的提升百分比——真实历史长度分布下的降幅与保留率为待验证项。**

逐例数据（每 case × 每策略：token 估算、保留明细、缺失事实清单、裁剪记录
与原因、摘要来源映射）：`backend/evals/artifacts/context-compression-v1-report.json`。

## 3. 复现命令

```bash
cd backend
python scripts/context_compression_eval.py          # 离线对照（本报告）
python -m pytest tests/test_context_compression_strategies.py   # 7 项确定性测试
```

真实模型对照（**需明确授权后执行**，交错三策略 × 8 case × N 重复，隔离用户，
保留失败与超预算，分别报告系统试验数与实际模型调用数）：

```bash
python scripts/context_compression_live.py --repetitions 3 \
    --out evals/artifacts/context-compression-live.json
```

## 4. 合成 vs 真实标记与未验证项

| 项 | 标记 |
|---|---|
| 三策略机制、预算对齐、溯源、over_budget、校验独立性 | **代码+确定性测试已验证**（7 测试） |
| 输入降幅 / 事实保留（本报告数字） | **合成数据离线结果**，不代表真实分布 |
| 真实模型下的输入降幅、必需事实保留、计划约束违规率 | **待验证**（脚本已备，未执行付费调用） |
| 输出质量退化（plan_constraint_violation_rate） | **待验证**（依赖真实生成） |
| 语义存活规则的独立复核 | 锚点规则复用 memory_grounded v0.3 冻结实现（同一预注册规则） |

**不生成简历指标**：在真实模型对照完成前，本批不产出任何"上下文压缩降低
X% token / 保持 Y% 事实"类的对外数字。
