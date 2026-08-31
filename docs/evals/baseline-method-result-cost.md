# Baseline → 方法 → 结果 → 成本：评测与可靠性证据总结

> 提交 `3a06df5`（2026-08-31）｜ 全部离线/Mock ｜ 真实模型项标注**待验证**

## 一、Baseline → 方法 → 结果 → 成本 总览

| 维度 | Baseline | 方法 | 结果（离线/Mock） | 成本 | 状态 |
|---|---|---|---|---|---|
| **系统门禁通过率** | 72.2%（初始，k=3） | 确定性修复+记忆预执行+GLM 禁思考 | 88.9%（k=3，[80.7,93.9]） | — | ✅ 已验证（历史实验） |
| **骨架贡献** | 真裸模型 72.4% | Agent 骨架（记忆+预执行+校验） | +20.9pp（p=0.032） | — | ✅ 显著 |
| **记忆层贡献** | OFF 82.2% | ON 86.5%（k=3） | +4.3pp（p=0.429） | ~65 token × 3/30 case | ❌ 不显著；价值=接地能力 |
| **上下文压缩** | full 基线 | 三策略（full/recent/relevant_summary） | recent 丢 28.6% 必需事实；relevant 保留 100% | 估算口径 | ✅ 分支逻辑（离线） |
| **恢复能力** | 无 checkpoint | planning-node durable checkpoint | 中断恢复 0 新增 planner 调用 | 节省 1 次生成/run | ✅ S2 实证 |
| **Planning 质量** | — | frozen metrics（A/B/C/D） | Mock 场景 10 项测试证明口径 | — | ✅ 口径冻结+测试 |
| **真实 token/费用/质量** | — | — | **待验证** | **待验证** | ⏳ 需授权 |

## 二、已验收项

### 2.1 报告一致性（Phase 1.1）
- 汇总表由 `generate_context_report_table.py` 从 JSON 工件自动生成
- `test_report_table_consistency` 断言文档与工件逐格相等（含分母一致性）
- **修复**：文档 recent 可发送 13/17 → 14/19（对齐 JSON）

### 2.2 Planning 质量成本指标（Phase 2，口径先冻结再实现）

| 指标 | 冻结定义 | 分母 |
|---|---|---|
| A. 首次合规率 | provenance=model_pass / 应生成计划的 trial | 含失败；澄清/拒绝单列 |
| B1. 格式修复成功率 | format_repair 可解析 / 进入格式修复的 trial | |
| B2. 确定性修复成功率 | deterministic_repair 通过规则 / 进入确定性修复 | 模板降级不算 |
| B3. LLM 修复成功率 | llm_repair 通过规则 / 进入 LLM 修复 | 模板/安全终止不算 |
| C. 最终合规计划率 | 交付合规计划 / 应生成计划 | 同时报降级/无计划/失败 |
| D. 成本 | 每请求操作类型/预算拒绝/发送/usage/耗时 | 未知保留 unknown |

10 项 Mock 场景测试证明每个分子分母正确。

### 2.3 Context 三层记录（Phase 3）
- Layer 1 源历史 → Layer 2 加载历史（30 任务/7 复盘上限）→ Layer 3 模型可见
- 加载阶段丢失与压缩阶段丢失分开计数
- 逐例数据在 `context-compression-v3-report.json` `history_layers` 字段

### 2.4 Checkpoint 恢复矩阵（Phase 4）
| 场景 | 注入点 | 新增 planner 调用 | 不变量 | 结果 |
|---|---|---|---|---|
| S2 checkpoint 已持久化→中断 | graceful shutdown | **0**（复用） | 1 plan, 0 dup | ✅ |
| S3 指纹改变 | 篡改 input_hash | ≥1（必须重新生成） | 不复用 | ✅ |
| S4 checkpoint 损坏 | 篡改 payload | ≥1（优雅降级） | 不崩溃 | ✅ |
| S5 重复恢复 | 两次恢复 | 0（第二次复用） | 1 plan, 0 dup | ✅ |

## 三、待人工复核

| 项 | 原因 |
|---|---|
| 21 条事实逐条评分 | AI 逐条审阅已提供（v3 report `fact_details`），未经独立人工复核 |
| rubric v10 D3/D4 kappa | worksheet 23 行已生成，待标注排期 |
| 真实 embedding 同义词分离 | 脚本就绪，owner 延期 |

## 四、待真实模型验证

| 指标 | 现状 | 需要什么 |
|---|---|---|
| 实际 token 用量 | Mock 值非真实 | live 脚本运行 |
| 实际费用 | 未测 | live + pricing |
| 生成质量（违规率） | 离线无模型输出 | live + validate_candidate |
| 重复任务率 | 需模型生成 | live |
| 真实语义召回 | 离线无 embedding | embedding 真实数据 |

## 五、简历候选结论表

| 结论 | 证据 | 统计范围 | 局限 | 当前可用 |
|---|---|---|---|---|
| 硬门禁 72.2→88.9%（k=3） | 实验 cd3eb74e | 30 case × 3 trial | 系统门禁≠Planning 合规率 | ✅ |
| P95 45.5→28.6s | 同上 | 90 trial | 离线无模型 | ✅ |
| Agent 骨架 +20.9pp（p=0.032） | 6b03e9af vs 85d6ba48 | k=1 双臂 | n=29/30 | ✅（带 CI） |
| 记忆层接地 0/3→3/3 | 消融 | 3 case | 非"质量提升" | ✅（限定措辞） |
| 记忆层 +13.3pp | **已撤回**（k=3 缩至 +4.3pp p=0.429） | — | — | ❌ 禁用 |
| 上下文压缩保留 100% 事实 | v3 离线 | 21 事实/11 case | 合成分布、分支逻辑 | ✅（限定离线） |
| 恢复 0 新增调用 | S2 测试 | n=1 Mock | 单次演示 | ✅（机制级） |

## 六、真实实验准备方案（待授权）

**冒烟（最小验证）**：3 trial × 1 case × 1 策略 = 3 次调用，约 ¥0.03
```bash
cd backend
python scripts/context_compression_live.py --repetitions 1 \
  --out /tmp/cc-smoke.jsonl
```
验证点：策略冻结断言、历史入模断言、计数>0、违规率可计算。

**全量对照**：11 case × 3 策略 × 3 重复 = 99 trial，约 99–200 次调用，预计 <¥2
- 隔离：每 trial 独立 guest 用户 + 历史数据
- 交错：策略轮转
- 停止条件：任何 trial 连续 3 次执行错误
- 输出：逐 trial JSONL + 汇总 JSON

**检索 embedding**：20 同义词对 + 10 对照，1 次 embedding 批调用
```bash
python scripts/synonym_separation_measurement.py
```

## 七、未执行项

1. 真实模型调用（全部）——待授权
2. 2 个 startup_recovery 测试失败（stale test-DB 终态事件，非生产代码 bug）
3. E2（runaway 护栏对照）——上轮审计遗留
4. 双进程 lease 集成测试——上轮审计遗留
