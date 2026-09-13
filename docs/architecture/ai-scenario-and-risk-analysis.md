# AI 场景与风险分析

| 项目 | 内容 |
|---|---|
| 版本 | v1.0 |
| 状态 | 设计基线 |
| 面向对象 | 架构师、AI 工程师、产品负责人、评审者 |
| 定位 | 判断 Career Planning Buddy 哪些场景适合 AI，哪些必须规则化或分流，并定义数据、知识库、模型、Prompt、RAG、Agent 的风险边界 |

English summary: AI scenario and risk analysis for Career Planning Buddy. It identifies suitable AI use cases, non-AI boundaries, model/data risks, safety triage, and mitigation controls.

---

## 1. 分析结论

Career Planning Buddy 适合使用 AI 的部分是“开放语义理解、上下文整合、候选计划生成、陪伴话术和质量评审”；不适合完全交给 AI 的部分是“业务写入、状态转移、权限、长期记忆落库、高风险处理、成本控制”。

核心原则：

| 原则 | 说明 |
|---|---|
| AI 生成候选，不直接决定事实 | 计划、任务、记忆都必须经过 schema 和规则校验 |
| Agent 只读，写入受控 | 只读工具由 Agent 调用，写入经 Service/persist |
| 高风险先分流 | 高风险内容不进入普通规划 |
| 证据可追踪 | 外部来源和经验原子必须能追溯 |
| 失败显式化 | 超时、预算、schema 错、证据不足都要有 fallback_reason |

## 2. AI 场景分级

| 场景 | 是否适合 AI | 方式 | 说明 |
|---|---|---|---|
| 用户意图识别 | 适合 | LLM 单次分类 + 置信度 | 输出 create_plan/replan/unsupported 和缺槽；查询走资源 API |
| 缺槽追问 | 适合部分 AI | 规则 + 模板/LLM | 问题数量和字段受控 |
| 上下文摘要 | 适合 | 程序拼接 + LLM 摘要可选 | 必须限长和脱敏 |
| 计划生成 | 适合 | CareerPlanningAgent | 唯一真 Agent，自主调只读工具 |
| 证据蒸馏 | 适合部分 AI | LLM/程序混合 | 需保留来源 |
| 任务质量校验 | 不完全适合 | 规则优先 + LLM Judge | starter_action、任务数、时长用规则 |
| 陪伴话术 | 适合 | LLM 单次生成 | 输出先审后发 |
| 记忆候选提取 | 适合 | LLM 候选 + 用户确认 | 敏感内容不自动入库 |
| 状态转移 | 不适合 AI | 纯规则 | 由状态机和 Service 控制 |
| 业务写入 | 不适合 AI | Service 事务 | Agent 不直接写库 |
| 高风险响应 | 不适合自由生成 | 固定模板 | 直接 safe_response |

## 3. AI 能力边界

### 3.1 可以做

- 理解自然语言规划请求。
- 根据用户档案、任务历史、复盘、记忆、来源生成候选计划。
- 生成“今天能开始”的任务和起步动作。
- 对计划质量做辅助评分。
- 生成温和、非评判式陪伴话术。
- 将用户反馈整理为候选记忆。

### 3.2 不能做

- 不能直接写入业务表。
- 不能绕过状态机改变任务/计划状态。
- 不能将敏感内容直接写入长期记忆。
- 不能提供心理、医疗、法律、金融等专业建议。
- 不能把外部网页内容当作系统指令执行。
- 不能在证据不足时假装确定。

## 4. 风险清单

| 风险 | 触发 | 影响 | 控制措施 |
|---|---|---|---|
| 幻觉计划 | LLM 编造岗位要求或学习路径 | 用户执行错误方向 | 来源标注、RAG、质量校验、fallback |
| 计划过重 | 一次生成过多任务 | 用户放弃 | 今日任务 1-3 个、时长限制、starter_action |
| 结构化输出失败 | LLM JSON/schema 不符 | 后端解析失败 | Pydantic 校验、重试 ≤1、降级 |
| 成本失控 | 工具调用和轮数过多 | 费用超标 | BudgetChecker、轮数/工具上限 |
| Prompt 注入 | 网页/检索内容含恶意指令 | 越权或输出污染 | evidence 标签、工具结果不进 system |
| 高风险漏判 | 用户表达心理危机/法律金融风险 | 安全事故 | 关键词 + LLM 分类器 + 固定分流 |
| 敏感记忆误存 | 用户透露隐私 | 合规风险 | candidate 池 + 用户确认 |
| 数据过期 | 岗位信息变化 | 建议不准 | 来源时间、搜索 provider、经验原子版本 |
| Mock 混入真实统计 | demo 数据污染 Eval | 指标失真 | `data_origin="mock"` |
| Provider 不稳定 | LLM/Search Provider 超时 | run 失败 | timeout、fallback_reason、降级链 |

## 5. 高风险分流边界

高风险内容包括但不限于：

| 类别 | 例子 | 处理 |
|---|---|---|
| 心理危机/自伤 | 明确自伤、自杀、极端绝望表达 | 固定支持话术 + 热线/资源 |
| 医疗 | 诊断、用药、治疗建议 | 拒绝专业建议，引导专业机构 |
| 法律 | 具体法律裁决、维权策略 | 不做法律判断 |
| 金融 | 投资、借贷、收益承诺 | 不做金融建议 |
| 重大人生风险 | 极端情绪下的冲动决策 | 降级为安全支持 |

触发后：

- 不进入普通规划链路；
- 不进入 `career_planning_agent`；
- 不写长期记忆；
- 不生成普通求职计划；
- Trace 只记录脱敏分类和分流结果。

## 6. 数据与知识库风险

| 数据源 | 风险 | 控制 |
|---|---|---|
| O*NET/ESCO | 海外职业分类与中国岗位语境不完全一致 | 用作技能 taxonomy，不直接当中文岗位事实 |
| Kaggle/公开岗位数据 | 可能过期、字段不统一、授权差异 | 只作趋势样本和离线分析，保留来源 |
| 手工 experience_atoms | 主观偏差 | 记录作者、来源、版本、适用 goal_type |
| Web Search | 来源质量不稳定 | 来源类型、可靠度、时间戳 |
| 用户复盘 | 主观、敏感 | 限长、脱敏、记忆确认 |

## 7. Prompt 与 RAG 风险

| 风险 | 约束 |
|---|---|
| Prompt 难以回放 | Prompt 文件必须版本化，旧版不改 |
| RAG 注入恶意指令 | 检索结果包裹为 evidence，不放 system message |
| 输出过度依赖单一来源 | 要求来源覆盖和经验原子交叉验证 |
| 过度泛化 | 按 goal_type 加 Prompt 和经验原子 |
| 证据不足仍给确定结论 | 输出 assumptions 和 fallback_reason |

## 8. Agent 风险

| 风险 | 约束 |
|---|---|
| Agent 越权调用工具 | ToolWhitelistGuard |
| Agent 循环不收敛 | max_tool_rounds=2、max_tool_calls_total=4、Stage 2/3/4 的 max_llm_calls=5/7、deadline 优先 |
| Agent 写库 | 禁止；写入只在 persist 节点 |
| Agent 输出半成品 | rule_validator + 一次受控 repair；quality_reviewer 默认离线 shadow |
| Agent 失败不可解释 | agent_steps/tool_calls Trace |

## 9. 评测要求

AI 场景必须用固定评测集覆盖：

| Case 类型 | 示例 |
|---|---|
| 正常规划 | 有完整 profile，生成今日任务 |
| 缺槽 | 缺 goal_type 或 available_time |
| 计划过重 | 用户时间很少，模型仍给过多任务 |
| 重规划 | 昨日任务失败，今日时间减少 |
| 高风险 | 风险输入进入 safe_response |
| 数据不足 | 没有来源或经验原子时降级 |
| Provider 超时 | LLM/Search timeout |
| Prompt 注入 | 检索结果含恶意指令 |

最低目标：30 case 固定数据集，首发场景通过率 ≥ 85%。

## 10. Go/No-Go 规则

进入真实模型阶段前必须满足：

| 条件 | 要求 |
|---|---|
| Provider PoC | structured output、timeout、错误格式验证通过 |
| Mock 纵切 | API/SSE/DB/Trace 跑通 |
| 安全分流 | 高风险 fixture 通过 |
| 成本预算 | 单 run cost 统计可查 |
| Eval smoke | 至少正常/缺槽/高风险/超时 case 通过 |

## 11. 关联文档

- [安全、审计与合规](../standards/security-and-compliance.md)
- [TDD 技术设计](./tdd.md)
- [PoC 验证报告](./poc-verification-report.md)
- [CareerPlanningAgent spec](../model-design/agent-nodes/career_planning_agent.spec.md)
- [数据与知识库设计说明](../model-design/data-seeding-and-sources.md)
