# 上下文管理效果验证 · 离线批次报告（v3 修订版）

> 2026-08-31 v3 ｜ 全部结果为**合成/离线**（零模型调用）｜ v3 口径见
> `docs/standards/metric-registry.md` ｜ 真实模型效果**待验证**

## v3 验收修复（七项，全部先复现后修复）

| # | 验收问题 | 复现证据 | 修复 |
|---|---|---|---|
| 1 | 预算检查不在共用发送入口 | `generate_agent_turn`（工具轮次）直接 `_complete_request` 无闸门；估算只含工具名+描述 | 闸门+逐请求记录（估算/预算/是否发送）移入 `_complete_request`——规划/工具/格式修复/业务修复四路共用；估算含完整工具 `input_json_schema`；压缩层与最终请求**两级超限分报** |
| 2 | 数字子串匹配与否定状态 | `"30" in "130" → True`；状态表无 收到/未收到 | digit-run **精确集合**比较；否定泛化（未X/没X）且**子句局部**判定（混合状态摘要不跨对象泄漏）；新增 30→130、2→20、收到→未收到、混合状态四反例（评分测试共 11 项）；评分证据改为**实际渲染请求的子句** |
| 3 | reviews 取旧弃新 | `retained_reviews = context.recent_reviews[:budget]` 无排序 | reviews 按 review_date desc 排序（乱序输入兼容）；docstring 写明 scheduled_date（规划语义"最近排期"）与仓库 updated_at（编辑时间代理）的区别 |
| 4 | 调用计数不可靠 | live wrapper 只包 2 个方法；拒绝会被计为已调 | 计数移至**发送边界**（`sent_request_count`，预算拒绝不增加；mock 等无边界计数 provider 回退到方法计数，私有属性不委托）；失败 trial 保留已发生的调用/用量/错误（`execution_error` 字段） |
| 5 | 评分条件与运行不一致 | live 评分重建 profile/日期（horizon/deadline 漂移） | 评分复用运行自身 `input_snapshot`（planning_window + 权威 completed_facts）与冻结配置；逐 trial 记录预算与请求记录；六分类统计（全部/产出计划/无计划/预算拒绝/运行失败/崩溃 + 产出计划违规率） |
| 6 | 校验独立性未经图节点 | 前测直接调函数、未验证渲染输入全段 | 新测试经**真实 `_validator_node`**：先断言目标旧事项从完整渲染输入的记录/摘要/completed_facts **全部消失**（用同一压缩算法+冻结策略重建渲染上下文），再把重复候选推进真实节点断言被拒 |
| 7 | 报告连续性 | — | v1/v2 保留并标注局限；新增独立留出集 cc-holdout-10/11（数字否定敏感、混合状态）；全样本/可发送样本分报 |

## v3 离线结果（11 case × 21 标注事实，含 2 独立留出）

<!-- AUTO-TABLE:BEGIN -->
| 策略 | 降幅（全样本） | 事实保留（全样本） | 保留（可发送样本） |
|---|---|---|---|
| full | 0.0% | **21/21** | **19/19** |
| recent | 8.8% | **15/21** | **14/19** |
| relevant_summary | 7.5% | **21/21** | **19/19** |
<!-- AUTO-TABLE:END -->

> 本表由 `scripts/generate_context_report_table.py` 从 v3 JSON 工件自动生成，
> 禁止手工编辑；一致性测试 `test_report_table_consistency` 断言文档与工件
> 逐格相等。

†可发送 = 最终请求未超预算（cc-budget-07 在 full 与 relevant_summary 下
显式超限：context 级与请求级均置位，真实链路由发送边界拒绝）。

留出集行为验证：cc-holdout-10 的"投递 30 家公司/收到 2 个邀约"在 recent
下因窗口外+数字精确判定 lost，在 relevant_summary 下经摘要保留——独立
留出集与回归集结论方向一致，无口径修改。

**边界声明**：离线相关性臂无真实 embedding（分支逻辑验证）；不预设任何
策略优劣断言；全部 21 条事实的逐条判定证据（子句原文/锚点数/覆盖率/
数字状态判定）在 `evals/artifacts/context-compression-v3-report.json`。

## 复现命令

```bash
cd backend
python scripts/context_compression_eval.py                  # v3 报告
python -m pytest tests/test_context_fact_retention.py \
                   tests/test_graph_validation_independence.py \
                   tests/test_context_compression_live_mock.py \
                   tests/test_llm_provider.py -k "budget"     # 链路证明
```

## 待真实模型验证

实际 token/费用/生成质量/违规率。live 脚本已按六/五项修复就绪，但**本轮
验收未运行付费调用**，不宣称真实实验就绪——需先跑一次小规模冒烟（3 trial）
确认计数与快照断言在真实 provider 下成立，再授权全量（11 case × 3 策略 ×
3 重复 ≈ 99 trial）。

---

# 以下为 v2 报告（已被 v3 取代，存档）

> 2026-08-31 v2 ｜ 全部结果为**合成/离线**（零模型调用）｜ v2 口径预注册于
> `docs/standards/metric-registry.md` ｜ 真实模型效果**待验证**
>
> **v1 报告废止说明**：v1（本文件下方保留）存在两处已被审计证实的缺陷——
> ① 保留率评分把未发送给模型的 summary_sources 来源清单计分；② 未定义
> 排序契约（数据集旧→新 vs 运行时最新在前，recent 窗口取错方向）。v1 的
> 保留率数字（recent 0.771 / relevant 1.00）**无效**，仅作过程记录。

## v2 修正内容（对应审计六项指控，全部核实为真并修复）

| # | 审计指控 | 修复 | 证据 |
|---|---|---|---|
| 1 | recent 取旧弃新（排序未定义） | 压缩内部按 scheduled_date desc 稳定排序（与运行时仓库一致），契约写入 docstring 与测试 | `test_windowed_strategies_share_the_same_budget`、stage6 契约测试更新 |
| 2 | 评分计入未发送的来源清单 | v2 评分对象=模型实收窗口（retained records + summary lines）；summary_sources 仅溯源 | `evals/context_metrics.py` + `test_fact_only_in_source_list_is_lost` |
| 3 | 窗口条数相同≠Token 预算相同 | 摘要参与收缩循环，两窗口策略优化**同一最终输入预算** | `context_compression.py` shrink 循环重构 |
| 4 | over_budget 未进执行流 | provider 调用前预算闸门：估算在**真实请求**上计算（`input_estimate` 随 provider 结果返回，graph 不再二次渲染），超限抛 `INPUT_BUDGET_EXCEEDED` 拒绝发送；压缩层 over_budget 进 state 与 trace | `test_input_budget_gate_refuses_request_before_sending`（计数 transport 断言零流量） |
| 5 | integrity=1.0 常量 | 真实链路测试：已完成事项被裁出模型输入后，重复安排它的候选经**真实 validate_candidate** 在三策略下全部被 RECENT_DUPLICATE 拒绝；覆盖边界（30 任务窗/20 事实截断）显式记录 | `test_validation_independence.py` |
| 6 | live 脚本三缺陷 | 重写：历史（Plan+Task+Review）真实装载且断言进入输入快照；策略在建 Run **前**注入并断言冻结快照前后一致；CountingProvider 精确计数；候选从持久化行（weekly_focus_json/tasks）还原；逐 trial JSONL 落盘保留失败 | `test_context_compression_live_mock.py`（Mock 端到端全过） |

## v2 离线结果（组件化评分，9 case × 3 策略）

| 策略 | 输入降幅均值* | 事实保留（宏观/微观） | needs_review | over-budget |
|---|---|---|---|---|
| full | 0%（基线） | 17/17（未压缩，定义使然） | 0 | 1/9† |
| recent | 9.3% | **0.630 / 12÷17** | 0 | 0/9 |
| relevant_summary | 7.9% | **1.000 / 17÷17** | 0 | 1/9† |

*估算口径（保守 CJK/Latin），非精确 tokenizer、非 provider 实际用量。
†cc-budget-07（400 token 极限预算）：full 与 relevant_summary 显式超限
（不裁剪、不静默）；真实执行链中该状态由调用前闸门拒绝发送。

**结论边界（重要）**：离线相关性臂未使用真实 embedding（无向量）——上述
差异只证明**分支逻辑**（近因窗口丢早而相关的事实；摘要折叠把它们保留在
模型实收文本中），**不构成真实语义召回效果的证明**。分母=17 条标注事实，
全部计入（无删例）；needs_review=0。

**与 v1 的数字差异来源**：recent 0.771→0.630（排序修正使旧相关事实真正
落出窗口）+ 评分修正；relevant 1.00→1.00 但语义变了（现在只对模型实收
文本测得，v1 是被来源清单污染的巧合）。

## 逐例数据与复现

```bash
cd backend
python scripts/context_compression_eval.py     # v2 报告（逐例明细内含）
python -m pytest tests/test_context_fact_retention.py                    tests/test_validation_independence.py                    tests/test_context_compression_live_mock.py   # 链路证明
python scripts/context_compression_live.py --repetitions 3       # 需授权
```

逐例：`evals/artifacts/context-compression-v2-report.json`（每 case ×
每策略：token 估算、事实判定明细含缺失原因、裁剪记录与原因、
promoted/budget_shrink/over_budget 状态、branch_check）。
输入快照与实验配置可核查点：Run 的 `config_snapshot_json
.context_compression_strategy` + `input_snapshot_json`（live 路径断言）。

## 待真实模型验证（未运行，不生成简历数字）

实际 token 用量（provider 口径）、费用、生成质量、计划约束违规率。
建议真实实验规模：9 case × 3 策略 × 3 重复 = 81 trial（约 81–160 次模型
调用，视修复轮而定；交错执行；隔离用户；预计费用 < ¥2）。执行需明确授权。

---

# 以下为 v1 报告原文（已废止，仅存档）

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
